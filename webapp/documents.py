"""Account-scoped document export queue and isolated worker helpers."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.db import IntegrityError
from django.db.models import F
from django.utils import timezone
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from .models import DocumentJob, Profile


class DocumentProblem(Exception):
    """An actionable export error that is safe to show to the candidate."""


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = (
    {"key": "classic", "label": "Classic", "description": "Stock-inspired banking layout with restrained rules.", "margin": "18mm", "margin_in": 0.71, "font_size": "10pt", "body_pt": 10, "name_pt": 24, "heading_pt": 13, "section_rule": True, "name_align": "center", "font": "Arial", "accent": "1F4E79", "stock": True},
    {"key": "modern", "label": "Modern", "description": "Clean left-aligned name and compact section rhythm.", "margin": "17mm", "margin_in": 0.67, "font_size": "10pt", "body_pt": 10, "name_pt": 25, "heading_pt": 13, "section_rule": False, "name_align": "left", "font": "Aptos", "accent": "2F5597", "stock": False},
    {"key": "compact", "label": "Compact", "description": "Dense layout for candidates with more experience.", "margin": "15mm", "margin_in": 0.59, "font_size": "9pt", "body_pt": 9, "name_pt": 22, "heading_pt": 12, "section_rule": False, "name_align": "left", "font": "Calibri", "accent": "333333", "stock": False},
    {"key": "executive", "label": "Executive", "description": "Roomier typography and stronger section separation.", "margin": "20mm", "margin_in": 0.79, "font_size": "10pt", "body_pt": 10, "name_pt": 27, "heading_pt": 14, "section_rule": True, "name_align": "left", "font": "Georgia", "accent": "1F1F1F", "stock": False},
    {"key": "minimal", "label": "Minimal", "description": "Neutral monochrome layout with minimal decoration.", "margin": "19mm", "margin_in": 0.75, "font_size": "10pt", "body_pt": 10, "name_pt": 24, "heading_pt": 12, "section_rule": False, "name_align": "center", "font": "Arial", "accent": "222222", "stock": False},
    {"key": "academic", "label": "Academic", "description": "More whitespace for education, research and long entries.", "margin": "18mm", "margin_in": 0.71, "font_size": "10pt", "body_pt": 10, "name_pt": 24, "heading_pt": 13, "section_rule": True, "name_align": "left", "font": "Times New Roman", "accent": "3F3F3F", "stock": False},
    {"key": "technical", "label": "Technical", "description": "Skills-forward, compact engineering-oriented layout.", "margin": "16mm", "margin_in": 0.63, "font_size": "9pt", "body_pt": 9, "name_pt": 23, "heading_pt": 12, "section_rule": True, "name_align": "left", "font": "Arial", "accent": "245A7A", "stock": False},
    {"key": "clean", "label": "Clean", "description": "Balanced ATS-safe layout for broad professional use.", "margin": "18mm", "margin_in": 0.71, "font_size": "10pt", "body_pt": 10, "name_pt": 24, "heading_pt": 13, "section_rule": False, "name_align": "center", "font": "Calibri", "accent": "15543C", "stock": False},
)
TEMPLATE_BY_KEY = {item["key"]: item for item in TEMPLATES}


def _compact_text(value, limit):
    value = " ".join(str(value or "").split())
    return value[:limit]


def _snapshot(profile: Profile, document_type: str, payload: dict) -> dict:
    data = json.loads(json.dumps(profile.data))
    extra = {}
    if document_type == DocumentJob.DocumentType.COVER_LETTER:
        body = str(payload.get("cover_letter_body") or "").strip()
        if not body:
            raise DocumentProblem("Add the cover-letter text before exporting it.")
        if len(body) > 20000:
            raise DocumentProblem("Cover-letter text must be 20,000 characters or fewer.")
        extra = {
            "body": body,
            "company": _compact_text(payload.get("company"), 200),
            "role": _compact_text(payload.get("role"), 200),
        }
    return {"profile": data, "extra": extra}


def prepare_document_request(profile: Profile, payload: dict) -> dict:
    if not profile.revision or not profile.data:
        raise DocumentProblem("Save your master profile before creating an export.")
    document_type = payload.get("document_type")
    output_format = payload.get("output_format")
    template_key = payload.get("template_key")
    if document_type not in DocumentJob.DocumentType.values:
        raise DocumentProblem("Choose CV or cover letter.")
    if output_format not in DocumentJob.OutputFormat.values:
        raise DocumentProblem("Choose PDF or DOCX.")
    if template_key not in TEMPLATE_BY_KEY:
        raise DocumentProblem("Choose one of the available ATS templates.")
    return {
        "document_type": document_type,
        "output_format": output_format,
        "template_key": template_key,
        "template": TEMPLATE_BY_KEY[template_key],
        "snapshot": _snapshot(profile, document_type, payload),
    }


def queue_document_job(user, profile: Profile, payload: dict):
    prepared = prepare_document_request(profile, payload)
    snapshot = prepared["snapshot"]
    fingerprint_payload = {
        "revision": profile.revision,
        "document_type": prepared["document_type"],
        "output_format": prepared["output_format"],
        "template_key": prepared["template_key"],
        "snapshot": snapshot,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    existing = DocumentJob.objects.filter(user=user, fingerprint=fingerprint).first()
    if existing:
        if existing.status == DocumentJob.Status.SUCCEEDED and (
            not artifact_file(existing) or not review_pdf_file(existing) or not review_digest(existing)
        ):
            mark_review_unavailable(existing)
            existing.refresh_from_db()
        return existing, False

    try:
        return DocumentJob.objects.create(
            user=user,
            profile_revision=profile.revision,
            document_type=prepared["document_type"],
            output_format=prepared["output_format"],
            template_key=prepared["template_key"],
            fingerprint=fingerprint,
            content_snapshot=snapshot,
        ), True
    except IntegrityError:
        return DocumentJob.objects.get(user=user, fingerprint=fingerprint), False


def retry_document_job(job: DocumentJob):
    if job.status != DocumentJob.Status.FAILED:
        raise DocumentProblem("Only failed exports can be retried.")
    updated = DocumentJob.objects.filter(
        pk=job.pk, status=DocumentJob.Status.FAILED, attempt=job.attempt,
    ).update(
        status=DocumentJob.Status.QUEUED, error="", artifact_name="", artifact_sha256="",
        verification={}, started_at=None, finished_at=None, updated_at=timezone.now(),
    )
    if not updated:
        raise DocumentProblem("This export attempt has already been retried. Reload to see its current status.")
    job.refresh_from_db()
    return job


def claim_next_job():
    """Atomically claim one job even on SQLite, which ignores SELECT FOR UPDATE."""
    while True:
        candidate = (
            DocumentJob.objects.filter(status=DocumentJob.Status.QUEUED)
            .order_by("created_at", "pk")
            .values("pk")
            .first()
        )
        if not candidate:
            return None
        now = timezone.now()
        claimed = DocumentJob.objects.filter(
            pk=candidate["pk"], status=DocumentJob.Status.QUEUED
        ).update(
            status=DocumentJob.Status.RUNNING,
            attempt=F("attempt") + 1,
            started_at=now,
            finished_at=None,
            error="",
            updated_at=now,
        )
        if claimed:
            return DocumentJob.objects.get(pk=candidate["pk"])


def artifact_file(job: DocumentJob):
    if not job.artifact_name:
        return None
    root = Path(settings.DOCUMENT_EXPORT_ROOT).resolve()
    candidate = (root / job.artifact_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def review_pdf_file(job: DocumentJob):
    """Return the exact PDF reviewed by the application gate.

    PDFs review themselves. DOCX exports retain the worker's LibreOffice proof
    PDF privately so the final editable file can be approved against its
    rendered text/order without exposing the proof as a download.
    """
    if job.output_format == DocumentJob.OutputFormat.PDF:
        return artifact_file(job)
    relative_name = (job.verification or {}).get("review_pdf_name")
    if not relative_name:
        return None
    root = Path(settings.DOCUMENT_EXPORT_ROOT).resolve()
    candidate = (root / relative_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def review_digest(job):
    if job.output_format == DocumentJob.OutputFormat.PDF:
        return job.artifact_sha256
    return (job.verification or {}).get("review_pdf_sha256")


def mark_review_unavailable(job):
    message = "The compiled file or its review proof changed or is missing. Retry this export."
    now = timezone.now()
    DocumentJob.objects.filter(pk=job.pk, status=DocumentJob.Status.SUCCEEDED, attempt=job.attempt,
                               artifact_sha256=job.artifact_sha256).update(
        status=DocumentJob.Status.FAILED, error=message, finished_at=now, updated_at=now,
    )
    return message


def read_review_pdf(job):
    proof = review_pdf_file(job)
    try:
        data = proof.read_bytes() if proof else b""
    except FileNotFoundError:
        data = b""
    if not data or hashlib.sha256(data).hexdigest() != review_digest(job):
        raise DocumentProblem(mark_review_unavailable(job))
    return data


def release_approval_matches(job: DocumentJob, user_id: int, profile_revision: int) -> bool:
    approval = (job.verification or {}).get("release_approval") or {}
    return bool(
        job.status == DocumentJob.Status.SUCCEEDED
        and job.artifact_sha256
        and approval.get("user_id") == user_id
        and approval.get("profile_revision") == profile_revision
        and approval.get("artifact_sha256") == job.artifact_sha256
        and review_digest(job) and approval.get("review_pdf_sha256") == review_digest(job)
    )


def draft_preview(proof: Path) -> io.BytesIO:
    """Show the actual rendered pages with a visible draft footer on every page."""
    label = PdfWriter().add_blank_page(width=150, height=15)
    label[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
        })}),
    })
    contents = DecodedStreamObject()
    contents.set_data(b"q 1 g 0 0 150 15 re f 0.25 g BT /F1 9 Tf 3 3 Td (DRAFT - review copy) Tj ET Q")
    label.replace_contents(contents)
    output = io.BytesIO()
    with proof.open("rb") as source:
        writer = PdfWriter()
        for original in PdfReader(source).pages:
            page = writer.add_page(original)
            if page.rotation:
                page.transfer_rotation_to_content()
            position = Transformation().translate(float(page.cropbox.left) + 8, float(page.cropbox.bottom) + 4)
            page.merge_transformed_page(label, position)
        writer.write(output)
    output.seek(0)
    return output


_LATEX_ESCAPE = str.maketrans({
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
})


def _tex(value):
    return str(value or "").translate(_LATEX_ESCAPE)


def _lines(value):
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _contact(profile):
    return [str(profile.get(key) or "").strip() for key in ("email", "phone", "location") if profile.get(key)]


def _split_name(value):
    parts = str(value or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def _stock_cv_latex(job: DocumentJob):
    profile = job.content_snapshot["profile"]
    first, last = _split_name(profile.get("full_name"))
    parts = [
        r"\documentclass[10pt,a4paper,sans]{moderncv}",
        r"\moderncvstyle{banking}",
        r"\moderncvcolor{blue}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[scale=0.82]{geometry}",
        rf"\name{{{_tex(first)}}}{{{_tex(last)}}}",
    ]
    if profile.get("location"):
        parts.append(rf"\address{{{_tex(profile['location'])}}}{{}}{{}}")
    if profile.get("phone"):
        parts.append(rf"\phone[mobile]{{{_tex(profile['phone'])}}}")
    if profile.get("email"):
        parts.append(rf"\email{{{_tex(profile['email'])}}}")
    parts += [r"\begin{document}", r"\makecvtitle"]
    if profile.get("summary"):
        parts += [r"\section{Summary}", rf"\cvitem{{}}{{{_tex(profile['summary'])}}}"]
    if profile.get("skills"):
        parts += [r"\section{Skills}", rf"\cvitem{{}}{{{_tex(', '.join(profile['skills']))}}}"]
    if profile.get("experience"):
        parts.append(r"\section{Experience}")
        for row in profile["experience"]:
            details = " ".join(_lines(row.get("details")))
            parts.append(
                rf"\cventry{{{_tex(row.get('dates'))}}}{{{_tex(row.get('title'))}}}"
                rf"{{{_tex(row.get('employer'))}}}{{}}{{}}{{{_tex(details)}}}"
            )
    if profile.get("education"):
        parts.append(r"\section{Education}")
        for row in profile["education"]:
            details = " ".join(_lines(row.get("details")))
            parts.append(
                rf"\cventry{{{_tex(row.get('dates'))}}}{{{_tex(row.get('degree'))}}}"
                rf"{{{_tex(row.get('school'))}}}{{}}{{}}{{{_tex(details)}}}"
            )
    parts.append(r"\end{document}")
    return "\n".join(parts)


def _stock_cover_latex(job: DocumentJob):
    profile = job.content_snapshot["profile"]
    extra = job.content_snapshot.get("extra", {})
    contact = " | ".join(_contact(profile))
    parts = [
        r"\documentclass[]{cover}",
        r"\usepackage{fancyhdr}",
        r"\pagestyle{fancy}",
        r"\fancyhf{}",
        r"\renewcommand{\headrulewidth}{0pt}",
        r"\begin{document}",
        rf"\namesection{{}}{{\Huge{{{_tex(profile.get('full_name'))}}}}}{{{_tex(contact)}}}",
        r"\currentdate{\today}",
    ]
    if extra.get("role") or extra.get("company"):
        target = " — ".join(filter(None, [extra.get("role"), extra.get("company")]))
        parts.append(rf"\lettercontent{{\textbf{{{_tex(target)}}}}}")
    for paragraph in _lines(extra.get("body")):
        parts.append(rf"\lettercontent{{{_tex(paragraph)}}}")
    parts += [
        r"\begin{flushright}",
        r"\closing{Kind regards,}",
        rf"\signature{{{_tex(profile.get('full_name'))}}}",
        r"\end{flushright}",
        r"\end{document}",
    ]
    return "\n".join(parts)


def _section(title, body, rule):
    if not body:
        return ""
    heading = rf"\section*{{{_tex(title)}}}"
    if rule:
        heading += r"\vspace{-0.65em}\hrule\vspace{0.45em}"
    return heading + "\n" + body + "\n"


def _article_latex(job: DocumentJob):
    profile = job.content_snapshot["profile"]
    extra = job.content_snapshot.get("extra", {})
    template = TEMPLATE_BY_KEY[job.template_key]
    alignment = "center" if template["name_align"] == "center" else "flushleft"
    parts = [
        rf"\documentclass[{template['font_size']},a4paper]{{article}}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        rf"\usepackage[margin={template['margin']}]{{geometry}}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\usepackage{parskip}",
        r"\usepackage{enumitem}",
        r"\usepackage{titlesec}",
        r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}",
        r"\setlist[itemize]{leftmargin=*,nosep}",
        r"\titleformat{\section}{\large\bfseries}{}{0pt}{}",
        r"\begin{document}",
        rf"\begin{{{alignment}}}",
        rf"{{\LARGE\textbf{{{_tex(profile.get('full_name'))}}}}}\\",
        _tex(" | ".join(_contact(profile))),
        rf"\end{{{alignment}}}",
        r"\vspace{0.5em}",
    ]
    if job.document_type == DocumentJob.DocumentType.COVER_LETTER:
        if extra.get("role") or extra.get("company"):
            parts += [
                rf"\textbf{{{_tex(extra.get('role'))}}}"
                + (rf" --- {_tex(extra.get('company'))}" if extra.get("company") else ""),
                r"\vspace{0.8em}",
            ]
        for paragraph in _lines(extra.get("body")):
            parts += [_tex(paragraph), ""]
        parts.append(r"\end{document}")
        return "\n".join(parts)

    if profile.get("summary"):
        parts.append(_section("Summary", "\n\n".join(_tex(x) for x in _lines(profile["summary"])), template["section_rule"]))
    if profile.get("skills"):
        parts.append(_section("Skills", _tex(", ".join(profile["skills"])), template["section_rule"]))
    if profile.get("experience"):
        rows = []
        for row in profile["experience"]:
            title = " · ".join(filter(None, [row.get("title"), row.get("employer")]))
            text = rf"\textbf{{{_tex(title)}}}"
            if row.get("dates"):
                text += rf"\hfill \textit{{{_tex(row['dates'])}}}"
            details = "\n\n".join(_tex(x) for x in _lines(row.get("details")))
            rows.append(text + ("\n" + details if details else ""))
        parts.append(_section("Experience", "\n\n".join(rows), template["section_rule"]))
    if profile.get("education"):
        rows = []
        for row in profile["education"]:
            title = " · ".join(filter(None, [row.get("school"), row.get("degree")]))
            text = rf"\textbf{{{_tex(title)}}}"
            if row.get("dates"):
                text += rf"\hfill \textit{{{_tex(row['dates'])}}}"
            details = "\n\n".join(_tex(x) for x in _lines(row.get("details")))
            rows.append(text + ("\n" + details if details else ""))
        parts.append(_section("Education", "\n\n".join(rows), template["section_rule"]))
    parts.append(r"\end{document}")
    return "\n".join(parts)


def _document_latex(job: DocumentJob):
    if job.template_key == "classic":
        if job.document_type == DocumentJob.DocumentType.CV:
            return _stock_cv_latex(job)
        return _stock_cover_latex(job)
    return _article_latex(job)


def _worker_payload(job: DocumentJob):
    return {
        "document_type": job.document_type,
        "template": TEMPLATE_BY_KEY[job.template_key],
        "snapshot": job.content_snapshot,
    }


def _prepare_worker_inputs(workdir: Path, job: DocumentJob):
    (workdir / "document.tex").write_text(_document_latex(job), encoding="utf-8")
    (workdir / "document.json").write_text(
        json.dumps(_worker_payload(job), ensure_ascii=False), encoding="utf-8"
    )
    if (
        job.output_format == DocumentJob.OutputFormat.PDF
        and job.document_type == DocumentJob.DocumentType.COVER_LETTER
        and job.template_key == "classic"
    ):
        source = REPO_ROOT / "cover_letters"
        shutil.copy2(source / "cover.cls", workdir / "cover.cls")
        shutil.copytree(source / "OpenFonts", workdir / "OpenFonts")


def _worker_command(workdir: Path, job: DocumentJob):
    runtime = settings.DOCUMENT_WORKER_RUNTIME
    command = [
        runtime, "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=128",
        f"--memory={settings.DOCUMENT_WORKER_MEMORY}",
        f"--cpus={settings.DOCUMENT_WORKER_CPUS}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
    ]
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    command += [
        "-e", "HOME=/tmp",
        "-v", f"{workdir.resolve()}:/work:rw",
        settings.DOCUMENT_WORKER_IMAGE,
        job.output_format,
        "lualatex" if job.document_type == DocumentJob.DocumentType.CV else "xelatex",
    ]
    return command


def _run_isolated_worker(workdir: Path, job: DocumentJob):
    try:
        result = subprocess.run(
            _worker_command(workdir, job),
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=settings.DOCUMENT_WORKER_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DocumentProblem("The document worker runtime is unavailable on this server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentProblem("The document compile exceeded its time limit.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f" ({detail[-1][:180]})" if detail else ""
        raise DocumentProblem("The isolated document worker could not compile this export." + suffix)
    output = workdir / f"output.{job.output_format}"
    if not output.is_file() or output.stat().st_size == 0:
        raise DocumentProblem("The document worker completed without producing an artifact.")
    return output


def _docx_text(path: Path):
    try:
        with zipfile.ZipFile(path) as doc:
            xml = doc.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DocumentProblem("The generated DOCX could not be read back for verification.") from exc
    root = ElementTree.fromstring(xml)
    return "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))


def _pdf_text(path: Path):
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise DocumentProblem("The generated PDF could not be read back for verification.") from exc
    return "\n".join(pages), len(pages)


def _pdf_page_count(path: Path):
    try:
        return len(PdfReader(str(path)).pages)
    except Exception as exc:
        raise DocumentProblem("The DOCX pagination preview could not be inspected.") from exc


def _contains_phone(text, phone):
    expected = "".join(ch for ch in str(phone or "") if ch.isdigit())
    actual = "".join(ch for ch in text if ch.isdigit())
    return not expected or len(expected) < 7 or expected in actual


def _verify_order(text: str, job: DocumentJob):
    profile = job.content_snapshot["profile"]
    expected = [profile.get("full_name", "")]
    if job.document_type == DocumentJob.DocumentType.CV:
        expected += [
            "Summary" if profile.get("summary") else "",
            "Skills" if profile.get("skills") else "",
            "Experience" if profile.get("experience") else "",
            "Education" if profile.get("education") else "",
        ]
    else:
        first_line = job.content_snapshot.get("extra", {}).get("body", "").splitlines()[0]
        expected += [first_line]
    lowered = text.lower()
    positions = [lowered.find(str(item).strip().lower()) for item in expected if str(item).strip()]
    return all(pos >= 0 for pos in positions) and positions == sorted(positions)


def verify_artifact(path: Path, job: DocumentJob):
    if job.output_format == DocumentJob.OutputFormat.PDF:
        text, pages = _pdf_text(path)
        pagination_source = "generated PDF"
    else:
        text = _docx_text(path)
        rendered_pdf = path.with_suffix(".pdf")
        if not rendered_pdf.is_file():
            raise DocumentProblem("The DOCX worker did not produce its pagination proof PDF.")
        pages = _pdf_page_count(rendered_pdf)
        pagination_source = "headless LibreOffice rendering of generated DOCX"

    normalized = " ".join(text.split())
    if len(normalized) < 20:
        raise DocumentProblem("The exported document has no usable text layer.")

    profile = job.content_snapshot["profile"]
    missing = []
    if profile.get("email") and str(profile["email"]).lower() not in normalized.lower():
        missing.append("email")
    if profile.get("phone") and not _contains_phone(normalized, profile["phone"]):
        missing.append("phone")
    if missing:
        raise DocumentProblem("The export is missing confirmed contact details: " + ", ".join(missing) + ".")
    if not _verify_order(text, job):
        raise DocumentProblem("The exported text order does not match the single-column source order.")

    warnings = []
    if job.document_type == DocumentJob.DocumentType.CV and pages > 3:
        warnings.append(f"CV is {pages} pages; review pagination before sending.")
    if job.document_type == DocumentJob.DocumentType.COVER_LETTER and pages > 2:
        warnings.append(f"Cover letter is {pages} pages; review pagination before sending.")
    return {
        "readable": True,
        "characters": len(normalized),
        "pages": pages,
        "pagination_source": pagination_source,
        "reading_order": "verified against the single-column source sequence",
        "contact_fields_checked": [key for key in ("email", "phone") if profile.get(key)],
        "warnings": warnings,
    }


def process_claimed_job(job: DocumentJob):
    if job.status != DocumentJob.Status.RUNNING:
        raise DocumentProblem("Only a claimed running job can be processed.")
    export_root = Path(settings.DOCUMENT_EXPORT_ROOT)
    staging_root = export_root / ".staging"
    staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=f"job-{job.pk}-", dir=staging_root) as temp:
            workdir = Path(temp)
            _prepare_worker_inputs(workdir, job)
            output = _run_isolated_worker(workdir, job)
            verification = verify_artifact(output, job)
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            destination = export_root / f"user-{job.user_id}" / f"job-{job.pk}"
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            final_name = f"{job.document_type}-{job.template_key}.{job.output_format}"
            final_path = destination / final_name
            proof = output.with_suffix(".pdf") if job.output_format == DocumentJob.OutputFormat.DOCX else None
            shutil.move(str(output), final_path)
            if proof is not None:
                if not proof.is_file():
                    raise DocumentProblem("The DOCX review proof is unavailable.")
                proof_path = destination / f"{job.document_type}-{job.template_key}.review.pdf"
                shutil.move(str(proof), proof_path)
                verification["review_pdf_name"] = str(proof_path.relative_to(export_root))
                verification["review_pdf_sha256"] = hashlib.sha256(proof_path.read_bytes()).hexdigest()
            verification.pop("release_approval", None)
            verification["sha256"] = digest
            job.status = DocumentJob.Status.SUCCEEDED
            job.error = ""
            job.artifact_name = str(final_path.relative_to(export_root))
            job.artifact_sha256 = digest
            job.verification = verification
            job.finished_at = timezone.now()
            job.save(update_fields=[
                "status", "error", "artifact_name", "artifact_sha256", "verification",
                "finished_at", "updated_at",
            ])
    except DocumentProblem as exc:
        job.status = DocumentJob.Status.FAILED
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
    except Exception:
        job.status = DocumentJob.Status.FAILED
        job.error = "Document export failed unexpectedly."
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
