"""Read candidate-selected documents into a draft. Raw uploads never become profile records."""
import csv
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

from .profile_data import MAX_ENTRIES, merge_profiles, unique_strings
from .resume_text import parse_resume

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_TEXT = 120_000
SUPPORTED_TYPES = {"pdf", "docx", "zip"}
LINKEDIN_FILES = {"profile.csv", "positions.csv", "education.csv", "skills.csv", "email addresses.csv"}


class ImportProblem(ValueError):
    pass


def _zip_members(raw, kind):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 2500:
            raise ImportProblem("This archive has too many files. Upload a smaller export or a résumé.")
        parts = {}
        budget = 16 * 1024 * 1024
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if "__MACOSX" in path.parts or entry.is_dir():
                continue
            if kind == "docx":
                key = path.as_posix()
                if not re.fullmatch(r"word/(?:document|header\d+|footer\d+)\.xml", key):
                    continue
            else:
                key = path.name.casefold()
                if key not in LINKEDIN_FILES:
                    continue
            if key in parts:
                raise ImportProblem("The archive contains duplicate profile files. Upload a single export.")
            if entry.file_size > min(8 * 1024 * 1024, budget):
                raise ImportProblem("The document is too large to read. Choose a shorter version.")
            budget -= entry.file_size
            parts[key] = archive.read(entry)
        return parts


def _docx_text(raw):
    from defusedxml import ElementTree

    parts = _zip_members(raw, "docx")
    if "word/document.xml" not in parts:
        raise ImportProblem("This file is not a Word document. Export it as .docx and try again.")
    lines = []
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    # Headers often contain contact details. Read them before body paragraphs, including tables.
    for key in sorted(parts, key=lambda k: ("header" not in k, "footer" in k, k)):
        root = ElementTree.fromstring(parts[key])
        for para in root.iter(ns + "p"):
            pieces = []
            for node in para.iter():
                if node.tag == ns + "t":
                    pieces.append(node.text or "")
                elif node.tag in {ns + "br", ns + "cr"}:
                    pieces.append("\n")
                elif node.tag == ns + "tab":
                    pieces.append("\t")
            lines.append("".join(pieces))
    return "\n".join(lines)


def _pdf_text(raw):
    from pypdf import PdfReader

    if b"%PDF-" not in raw[:1024]:
        raise ImportProblem("This file is not a readable PDF. Export a fresh copy and try again.")
    pdf = PdfReader(io.BytesIO(raw))
    if pdf.is_encrypted:
        raise ImportProblem("This PDF is password-protected. Upload an unlocked copy.")
    if len(pdf.pages) > 40:
        raise ImportProblem("Choose a résumé or profile PDF with 40 pages or fewer.")
    pages, size = [], 0
    for page in pdf.pages:
        content = page.extract_text() or ""
        size += len(content)
        if size > MAX_TEXT:
            raise ImportProblem("This document has too much text. Choose a shorter résumé.")
        pages.append(content)
    return "\n".join(pages)


def _linkedin(raw):
    parts = _zip_members(raw, "zip")
    if not parts:
        raise ImportProblem("This ZIP has no LinkedIn profile CSVs. Choose your LinkedIn data export.")

    def rows(name):
        data = parts.get(name + ".csv", b"").decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(data, newline=""))
        for row in reader:
            yield {k.strip().casefold(): (v or "").strip() for k, v in row.items() if k}

    profile = next(rows("profile"), {})
    draft = {
        "full_name": " ".join(filter(None, (profile.get("first name"), profile.get("last name")))),
        "headline": profile.get("headline", ""), "summary": profile.get("summary", ""),
        "location": profile.get("geo location") or profile.get("location", ""),
        "skills": unique_strings(row.get("name", "") for row in rows("skills")),
    }
    emails = list(rows("email addresses"))
    primary = next((r for r in emails if r.get("primary", "").lower() in {"yes", "true"}), None)
    draft["email"] = (primary or (emails[0] if emails else {})).get("email address") or profile.get("email address", "")
    draft["experience"] = []
    for row in rows("positions"):
        job = {"title": row.get("title", ""), "employer": row.get("company name", ""),
               "dates": " – ".join(filter(None, (row.get("started on"), row.get("finished on")))),
               "details": "\n".join(filter(None, (row.get("location"), row.get("description"))))}
        if any(job.values()):
            draft["experience"].append(job)
    draft["education"] = []
    for row in rows("education"):
        entry = {"school": row.get("school name", ""),
                 "degree": ", ".join(filter(None, (row.get("degree name"), row.get("field of study")))),
                 "dates": " – ".join(filter(None, (row.get("start date"), row.get("end date")))),
                 "details": "\n".join(filter(None, (row.get("notes"), row.get("activities"))))}
        if any(entry.values()):
            draft["education"].append(entry)
    return draft


def parse_document(kind, raw, *, ai=False):
    if kind not in SUPPORTED_TYPES or not 0 < len(raw) <= MAX_UPLOAD_BYTES:
        raise ImportProblem("Choose a nonempty PDF, DOCX or LinkedIn ZIP up to 8 MiB.")
    if kind == "zip":
        draft = _linkedin(raw)
    else:
        text = _pdf_text(raw) if kind == "pdf" else _docx_text(raw)
        if not text.strip():
            raise ImportProblem("No selectable text was found. Upload a text PDF or DOCX, or enter your details manually.")
        if len(text) > MAX_TEXT:
            raise ImportProblem("This document has too much text. Choose a shorter résumé.")
        draft = parse_resume(text)
        if ai:
            from .ai_extract import extract_draft
            draft = extract_draft(text, draft)
    if not any(draft.values()):
        raise ImportProblem("No profile fields were found. Try another export or enter your details manually.")
    draft = merge_profiles({}, draft)
    if len(json.dumps(draft, ensure_ascii=False)) > MAX_TEXT or any(len(draft[k]) > MAX_ENTRIES for k in ("experience", "education")):
        raise ImportProblem("Too many profile entries. Choose a shorter résumé or export.")
    return draft


def read_upload(upload, *, ai=False):
    # Parse off the web process so an expensive or malformed document cannot tie it up indefinitely.
    kind = upload.name.rsplit(".", 1)[-1].lower()
    try:
        run = subprocess.run(
            [sys.executable, "-m", "webapp.importers", kind, *(["--ai"] if ai else [])], input=upload.read(MAX_UPLOAD_BYTES + 1),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=Path(__file__).resolve().parents[1],
            timeout=45 if ai else 12, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ImportProblem("The document took too long to read. Try a shorter copy.") from None
    if run.returncode == 2:
        raise ImportProblem(json.loads(run.stdout))
    if run.returncode != 0:
        raise ImportProblem("This file could not be read. Export a fresh copy or enter your details manually.")
    return json.loads(run.stdout)


def _worker(kind, *, ai=False):
    if sys.platform.startswith("linux"):
        import resource
        # Keep the stricter limit if the operator already set one.
        for limit, value in ((resource.RLIMIT_AS, 512 * 1024 * 1024), (resource.RLIMIT_CPU, 8)):
            soft, hard = resource.getrlimit(limit)
            cap = min([value] + [n for n in (soft, hard) if n != resource.RLIM_INFINITY])
            resource.setrlimit(limit, (cap, hard))
    try:
        profile = parse_document(kind, sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1), ai=ai)
    except ImportProblem as exc:
        print(json.dumps(str(exc)))
        return 2
    except Exception:
        # Malformed XML, PDFs and archives all take the same user-visible recovery path.
        return 1
    print(json.dumps(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker(sys.argv[1], ai="--ai" in sys.argv[2:]))
