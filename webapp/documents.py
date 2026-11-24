"""Account-scoped document export queue and isolated worker helpers."""
from __future__ import annotations

import hashlib
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
from pypdf import PdfReader

from .models import DocumentJob, Profile


class DocumentProblem(Exception):
    """An actionable export error that is safe to show to the candidate."""


TEMPLATES = (
    {"key": "classic", "label": "Classic", "description": "Traditional headings with generous spacing.", "margin": "18mm", "font_size": "10pt", "section_rule": True},
    {"key": "modern", "label": "Modern", "description": "Crisp headings and balanced whitespace.", "margin": "17mm", "font_size": "10pt", "section_rule": False},
    {"key": "compact", "label": "Compact", "description": "Tighter spacing for dense experience.", "margin": "15mm", "font_size": "9pt", "section_rule": False},
    {"key": "executive", "label": "Executive", "description": "Roomy layout for senior profiles.", "margin": "20mm", "font_size": "10pt", "section_rule": True},
    {"key": "minimal", "label": "Minimal", "description": "Simple typography with no decorative rules.", "margin": "19mm", "font_size": "10pt", "section_rule": False},
    {"key": "academic", "label": "Academic", "description": "Long-form layout for education and research.", "margin": "18mm", "font_size": "10pt", "section_rule": True},
    {"key": "technical", "label": "Technical", "description": "Compact skills-first layout for engineering roles.", "margin": "16mm", "font_size": "9pt", "section_rule": True},
    {"key": "clean", "label": "Clean", "description": "Neutral ATS-safe layout for broad use.", "margin": "18mm", "font_size": "10pt", "section_rule": False},
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


def queue_document_job(user, profile: Profile, payload: dict):
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

    snapshot = _snapshot(profile, document_type, payload)
    fingerprint_payload = {
        "revision": profile.revision,
        "document_type": document_type,
        "output_format": output_format,
        "template_key": template_key,
        "snapshot": snapshot,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    existing = DocumentJob.objects.filter(user=user, fingerprint=fingerprint).first()
    if existing:
        if existing.status == DocumentJob.Status.SUCCEEDED and not artifact_file(existing):
            existing.status = DocumentJob.Status.FAILED
            existing.error = "The previous artifact is no longer available. Retry the export."
            existing.finished_at = timezone.now()
            existing.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return existing, False

    try:
        return DocumentJob.objects.create(
            user=user,
            profile_revision=profile.revision,
            document_type=document_type,
            output_format=output_format,
            template_key=template_key,
            fingerprint=fingerprint,
            content_snapshot=snapshot,
        ), True
    except IntegrityError:
        # A concurrent identical request won the unique (user, fingerprint) race.
        return DocumentJob.objects.get(user=user, fingerprint=fingerprint), False


def retry_document_job(job: DocumentJob):
    if job.status != DocumentJob.Status.FAILED:
        raise DocumentProblem("Only failed exports can be retried.")
    job.status = DocumentJob.Status.QUEUED
    job.error = ""
    job.artifact_name = ""
    job.artifact_sha256 = ""
    job.verification = {}
    job.started_at = None
    job.finished_at = None
    job.save(update_fields=[
        "status", "error", "artifact_name", "artifact_sha256", "verification",
        "started_at", "finished_at", "updated_at",
    ])
    return job


def claim_next_job():
    """Atomically claim one job even when the deployment uses SQLite.

    SQLite ignores SELECT ... FOR UPDATE, so use a compare-and-swap UPDATE on
    the queued status. Multiple workers may observe the same candidate, but
    only one can transition it from queued to running.
    """
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


def _document_markdown(job: DocumentJob):
    profile = job.content_snapshot["profile"]
    extra = job.content_snapshot.get("extra", {})
    lines = [f"# {profile.get('full_name', '')}", " · ".join(_contact(profile)), ""]
    if job.document_type == DocumentJob.DocumentType.COVER_LETTER:
        if extra.get("role") or extra.get("company"):
            lines += [f"**{extra.get('role', '')}{' — ' if extra.get('role') and extra.get('company') else ''}{extra.get('company', '')}**", ""]
        lines += [extra.get("body", ""), ""]
        return "\n".join(lines)

    if profile.get("summary"):
        lines += ["## Summary", profile["summary"], ""]
    if profile.get("skills"):
        lines += ["## Skills", ", ".join(profile["skills"]), ""]
    if profile.get("experience"):
        lines += ["## Experience"]
        for row in profile["experience"]:
            title = " · ".join(filter(None, [row.get("title"), row.get("employer")]))
            lines += [f"**{title}**", row.get("dates", ""), row.get("details", ""), ""]
    if profile.get("education"):
        lines += ["## Education"]
        for row in profile["education"]:
            title = " · ".join(filter(None, [row.get("school"), row.get("degree")]))
            lines += [f"**{title}**", row.get("dates", ""), row.get("details", ""), ""]
    return "\n".join(str(line or "") for line in lines)


def _section(title, body, rule):
    if not body:
        return ""
    heading = rf"\section*{{{_tex(title)}}}"
    if rule:
        heading += r"\vspace{-0.65em}\hrule\vspace{0.45em}"
    return heading + "\n" + body + "\n"


def _document_latex(job: DocumentJob):
    profile = job.content_snapshot["profile"]
    extra = job.content_snapshot.get("extra", {})
    template = TEMPLATE_BY_KEY[job.template_key]
    rule = template["section_rule"]
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
        r"\begin{center}",
        rf"{{\LARGE\textbf{{{_tex(profile.get('full_name'))}}}}}\\",
        _tex(" | ".join(_contact(profile))),
        r"\end{center}",
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
        parts.append(_section("Summary", "\n\n".join(_tex(x) for x in _lines(profile["summary"])), rule))
    if profile.get("skills"):
        parts.append(_section("Skills", _tex(", ".join(profile["skills"])), rule))
    if profile.get("experience"):
        rows = []
        for row in profile["experience"]:
            title = " · ".join(filter(None, [row.get("title"), row.get("employer")]))
            text = rf"\textbf{{{_tex(title)}}}"
            if row.get("dates"):
                text += rf"\hfill \textit{{{_tex(row['dates'])}}}"
            details = "\n\n".join(_tex(x) for x in _lines(row.get("details")))
            rows.append(text + ("\n" + details if details else ""))
        parts.append(_section("Experience", "\n\n".join(rows), rule))
    if profile.get("education"):
        rows = []
        for row in profile["education"]:
            title = " · ".join(filter(None, [row.get("school"), row.get("degree")]))
            text = rf"\textbf{{{_tex(title)}}}"
            if row.get("dates"):
                text += rf"\hfill \textit{{{_tex(row['dates'])}}}"
            details = "\n\n".join(_tex(x) for x in _lines(row.get("details")))
            rows.append(text + ("\n" + details if details else ""))
        parts.append(_section("Education", "\n\n".join(rows), rule))
    parts.append(r"\end{document}")
    return "\n".join(parts)


def _worker_command(workdir: Path, job: DocumentJob):
    runtime = settings.DOCUMENT_WORKER_RUNTIME
    command = [
        runtime, "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=128",
        f"--memory={settings.DOCUMENT_WORKER_MEMORY}",
        f"--cpus={settings.DOCUMENT_WORKER_CPUS}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
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
        raise DocumentProblem("The isolated document worker could not compile this export.")
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
        expected += [job.content_snapshot.get("extra", {}).get("body", "").splitlines()[0]]
    lowered = text.lower()
    positions = [lowered.find(str(item).strip().lower()) for item in expected if str(item).strip()]
    return all(pos >= 0 for pos in positions) and positions == sorted(positions)


def verify_artifact(path: Path, job: DocumentJob):
    if job.output_format == DocumentJob.OutputFormat.PDF:
        text, pages = _pdf_text(path)
    else:
        text, pages = _docx_text(path), None
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
    if pages and job.document_type == DocumentJob.DocumentType.CV and pages > 3:
        warnings.append(f"CV is {pages} pages; review pagination before sending.")
    if pages and job.document_type == DocumentJob.DocumentType.COVER_LETTER and pages > 2:
        warnings.append(f"Cover letter is {pages} pages; review pagination before sending.")
    return {
        "readable": True,
        "characters": len(normalized),
        "pages": pages,
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
            (workdir / "document.tex").write_text(_document_latex(job), encoding="utf-8")
            (workdir / "document.md").write_text(_document_markdown(job), encoding="utf-8")
            output = _run_isolated_worker(workdir, job)
            verification = verify_artifact(output, job)
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            destination = export_root / f"user-{job.user_id}" / f"job-{job.pk}"
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            final_name = f"{job.document_type}-{job.template_key}.{job.output_format}"
            final_path = destination / final_name
            shutil.move(str(output), final_path)
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
