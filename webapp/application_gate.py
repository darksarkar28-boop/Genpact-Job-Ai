"""Fail-closed pre-export checks for account-owned application documents.

No content is sent to an LLM, stored in a session, or logged. Verification is
deterministic: no algorithm here can establish every implied biographical claim.
We block ungrounded high-risk facts and require exact source-backed company claims.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from pypdf import PdfReader
from tools.ats_coverage import evaluate, has_term, normalized

MAX_PDF = 8 * 1024 * 1024
MAX_SOURCE = 1_048_576
NUMBER = re.compile(r"(?<!\w)(?:[$€£₹]\s*)?\d+(?:[.,]\d+)?\s*(?:%|percent|million|billion|thousand|[kKmM]\b|users\b|clients\b|employees\b|people\b|projects\b|engineers\b)", re.I)
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
DEGREE = re.compile(r"\b(?:B\.?\s?Sc|M\.?\s?Sc|B\.?\s?Tech|M\.?\s?Tech|MBA|Ph\.?\s?D|Bachelor(?:'s)?|Master(?:'s)?|Doctorate)\b", re.I)
ROLE = re.compile(r"^\s*(?:job title|position|role)\s*:\s*(.+)$", re.I)
EMPLOYER = re.compile(r"\b(?i:(?:worked|working|employed|engineer|analyst|manager|developer)\s+at)\s+([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,4})")
EXPERIENCE_HEADINGS = {"experience", "work experience", "professional experience", "employment", "work history", "employment history"}
OTHER_HEADINGS = {"summary", "profile", "education", "skills", "projects", "certifications", "awards", "languages", "publications"}
COMPANY_FACT = re.compile(r"\b(?:founded|launched|announced|raised|acquired|operates|headquartered|reported|increased|decreased|employs)\b", re.I)
NAMED_COMPANY_FACT = re.compile(
    r"\b(?!We\b|Our\b|They\b|My\b)[A-Z][\w&.-]+(?:\s+[A-Z][\w&.-]+){0,4}\s+(?:has\s+)?"
    r"(?:founded|launched|announced|raised|acquired|operates|reported|increased|decreased|employs|is|has|provides|serves|develops)\b",
    0,
)


class GateError(ValueError):
    pass


def parse_json_list(value, label, *, maximum=150):
    try:
        rows = json.loads(value or "[]")
    except (ValueError, TypeError) as exc:
        raise GateError(f"{label} must be a JSON list") from exc
    if not isinstance(rows, list) or len(rows) > maximum:
        raise GateError(f"{label} must contain at most {maximum} records")
    return rows


def profile_text(profile):
    """Only the authenticated candidate's confirmed saved profile is evidence."""
    scalars = ("full_name", "headline", "email", "phone", "location", "summary")
    parts = [str(profile.get(field, "")) for field in scalars]
    parts.extend(x for x in profile.get("skills", []) if isinstance(x, str))
    for group in ("experience", "education"):
        for row in profile.get(group, []):
            if isinstance(row, dict):
                parts.extend(str(row.get(k, "")) for k in ("title", "employer", "school", "degree", "dates", "details"))
    return "\n".join(parts)


def pdf_text(content, label):
    if not content or len(content) > MAX_PDF or not content.startswith(b"%PDF-"):
        raise GateError(f"{label}: upload a valid PDF of at most 8 MiB")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted or len(reader.pages) not in (1, 2):
            raise GateError(f"{label}: document must be unencrypted and one or two pages")
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    except GateError:
        raise
    except Exception as exc:
        raise GateError(f"{label}: PDF text extraction failed") from exc
    cleaned = normalized(extracted)
    if len(cleaned) < 40:
        raise GateError(f"{label}: missing or unreadable PDF text layer (scanned PDFs are not supported)")
    if "\ufffd" in extracted or re.search(r"\(cid:\d+\)", extracted, re.I):
        raise GateError(f"{label}: corrupt text-layer glyphs prevent reliable ATS parsing")
    return extracted


def section_order(extracted, headings):
    """Compare the caller's visual section order to the machine text layer."""
    if not isinstance(headings, list) or len(headings) < 2 or len(headings) > 12:
        return "List at least two visual section headings in their displayed order"
    norm = normalized(extracted)
    seen = []
    for heading in headings:
        if not isinstance(heading, str) or not heading.strip() or len(heading) > 80:
            return "Invalid heading in expected reading order"
        matches = list(re.finditer(r"(?<!\w)" + re.escape(normalized(heading)) + r"(?!\w)", norm))
        if len(matches) != 1:
            return f"Expected heading {heading!r} must appear exactly once in extracted text"
        seen.append(matches[0].start())
    if seen != sorted(seen) or len(set(seen)) != len(seen):
        return "Extracted heading order does not match the PDF's stated visual order"
    return None


def _profile_fields(profile, key):
    section = "experience" if key in ("employer", "title") else "education"
    return [str(row.get(key, "")).strip() for row in profile.get(section, []) if isinstance(row, dict) and row.get(key)]


def _standalone_title_like(text):
    """Detect a short title-case role heading, not narrative or numeric bullets.

    In this deterministic gate, a potential role title without an employer must
    match one of the user's confirmed job titles before it may be released.
    """
    words = text.strip().split()
    return (1 <= len(words) <= 7 and all(
        word[0].isupper() or word.casefold() in {"of", "and", "the", "for", "in", "&"}
        for word in words
    ) and not any(ch.isdigit() for ch in text))


def _position_finding(line, in_experience, titles, positions, stock_headings, next_line):
    label = ROLE.match(line)
    if not label and not in_experience:
        return None
    claim = (label.group(1) if label else line).strip()
    # The stock moderncv text layer puts the employer on its own line,
    # followed by the title and optional dates from the same experience row.
    employer = normalized(claim)
    if in_experience and not label and employer in stock_headings:
        if normalized(next_line) in stock_headings[employer]:
            return None
        return f"Unsupported job title and employer: {next_line[:100]} at {claim[:100]}"
    # Article and Word layouts put the title and employer on one line.
    parts = re.split(r"\s+at\s+|\s*·\s*|\s+[-–—]\s+", claim, maxsplit=1, flags=re.I)
    if len(parts) == 1:
        if label and normalized(claim) not in titles:
            return "Unsupported job title: " + claim[:100]
        if not label and in_experience and _standalone_title_like(claim) and normalized(claim) not in titles:
            return "Unsupported job title: " + claim[:100]
        return None

    title, employer = map(normalized, parts)
    # Free prose can mention a workplace. Only a label, a known title, or a
    # standalone role heading declares that the candidate held this position.
    if not label and title not in titles:
        words = parts[0].split()
        if not words or words[0].casefold() in {"i", "we", "my", "our"} or not all(
            word[0].isupper() or word.casefold() in {"of", "and", "the", "for", "in", "&"}
            for word in words
        ):
            return None
    if (title, employer) not in positions:
        return "Unsupported job title and employer: " + claim[:160]
    return None


def _company_fact(line, employer):
    return COMPANY_FACT.search(line) and (
        (employer and has_term(normalized(line), employer)) or NAMED_COMPANY_FACT.search(line)
    )


def _statement_is_verified(line, quotes):
    statement = line.strip().rstrip(".!?")
    return any(has_term(quote, statement) for quote in quotes)


def candidate_findings(document, profile, claim_list, externally_verified="", employer=""):
    """Require high-risk tokens and declared claims to be in confirmed profile evidence.

    A source quote only grounds its exact text, not an inference or exaggeration.
    """
    evidence = normalized(profile_text(profile))
    public_quotes = [normalized(quote) for quote in externally_verified.splitlines()]
    positions = set()
    stock_headings = {}
    for row in profile.get("experience", []):
        if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not row["title"].strip():
            continue
        title = normalized(row["title"])
        workplace = normalized(str(row.get("employer") or ""))
        positions.add((title, workplace))
        if workplace:
            dates = str(row.get("dates") or "")
            positions.add((title, normalized(f"{workplace} {dates}")))
            headings = stock_headings.setdefault(workplace, set())
            headings.add(title)
            headings.add(normalized(f"{title} {dates}"))
    titles = {title for title, _ in positions}
    degrees = _profile_fields(profile, "degree")
    confirmed_qualifications = {normalized(degree) for degree in degrees}
    for row in profile.get("education", []):
        if isinstance(row, dict) and row.get("degree") and row.get("dates"):
            degree, dates = row["degree"], row["dates"]
            confirmed_qualifications.update((
                normalized(f"{degree} {dates}"), normalized(f"{degree} ({dates})"),
            ))
    known_degree_labels = {
        normalized(match.group(0))
        for degree in degrees
        for match in DEGREE.finditer(degree)
    }
    findings = []
    in_experience = False
    lines = [text.strip() for text in document.splitlines() if text.strip()]
    for line_number, line in enumerate(lines):
        next_line = lines[line_number + 1] if line_number + 1 < len(lines) else ""
        if not (public_quotes and _company_fact(line, employer) and _statement_is_verified(line, public_quotes)):
            for match in (*YEAR.finditer(line), *NUMBER.finditer(line)):
                token = match.group().strip()
                if not has_term(evidence, token):
                    findings.append(f"Unsupported metric or date: {token}")
        heading = normalized(line).rstrip(":")
        if heading in EXPERIENCE_HEADINGS:
            in_experience = True
            continue
        elif heading in OTHER_HEADINGS:
            in_experience = False
            continue
        degree_tokens = list(DEGREE.finditer(line))
        for index, degree_token in enumerate(degree_tokens):
            # Match the *whole qualification* against one saved degree record.
            # Looking only at BSc/PhD prefixes allows `BSc Computer Science,
            # BSc Physics` when the candidate confirmed only the former.
            end = degree_tokens[index + 1].start() if index + 1 < len(degree_tokens) else len(line)
            qualification = line[degree_token.start():end]
            if normalized(qualification.rstrip(" .,;|•/")) in confirmed_qualifications:
                continue
            # School, dates and punctuation are presentation metadata, not
            # part of the qualification. Another degree token always creates
            # another claim, even when it has the same credential prefix.
            qualification = re.split(
                r"\s*(?:[,;|•]|\s+[—–]\s+)\s*|\s+(?:at|from)\s+",
                qualification, maxsplit=1, flags=re.I,
            )[0]
            qualification = re.sub(r"\s+(?:and|&)\s*$", "", qualification, flags=re.I).strip(" -–—,;|•/")
            if (normalized(qualification) not in confirmed_qualifications
                    and normalized(qualification.rstrip(".)]}")) not in confirmed_qualifications):
                kind = ("Unsupported degree credential" if normalized(degree_token.group(0))
                        not in known_degree_labels else "Unsupported degree/course")
                findings.append(f"{kind}: {qualification[:120]}")
        position_error = _position_finding(line, in_experience, titles, positions, stock_headings, next_line)
        if position_error:
            findings.append(position_error)
        for match in EMPLOYER.finditer(line):
            if not any(has_term(normalized(match.group(1)), emp) for emp in _profile_fields(profile, "employer")):
                findings.append("Unsupported employer: " + match.group(1)[:100])
    for i, entry in enumerate(claim_list):
        if not isinstance(entry, dict) or not isinstance(entry.get("claim"), str) or not isinstance(entry.get("evidence"), str):
            findings.append(f"Candidate claim #{i + 1} needs claim and evidence text")
            continue
        claim, quote = entry["claim"].strip(), entry["evidence"].strip()
        if not has_term(normalized(document), claim) or not has_term(evidence, quote) or not has_term(normalized(quote), claim):
            findings.append(f"Candidate claim #{i + 1} needs matching wording in the draft and its confirmed evidence: {claim[:160]}")
    return list(dict.fromkeys(findings))


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def fetch_public_source(url):
    """Only fetch public HTTPS directly, with DNS-pinned TLS; no redirects/SSRF."""
    try:
        parsed = urlsplit(url)
        valid_url = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.password and parsed.port in (None, 443))
        host = parsed.hostname.encode("idna").decode("ascii") if valid_url else ""
    except (ValueError, UnicodeError) as exc:
        raise GateError("Company citation has an invalid HTTPS URL") from exc
    if not valid_url:
        raise GateError("Company citation must be a direct public HTTPS URL")
    if "." not in host:
        raise GateError("Company citation must use a public hostname")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise GateError("Company citation resolves to a non-public address")
        ip = sorted(addresses)[0]

        class Pinned(http.client.HTTPSConnection):
            def connect(self):
                raw = socket.create_connection((ip, 443), timeout=5)
                self.sock = self._context.wrap_socket(raw, server_hostname=host)

        conn = Pinned(host, timeout=5, context=ssl.create_default_context())
        try:
            route = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            conn.request("GET", route, headers={"Host": host, "User-Agent": "JobAIGrounding/1.0"})
            response = conn.getresponse()
            if response.status != 200:
                raise GateError("Citation URL did not return HTTP 200; redirects are not followed")
            if not response.getheader("Content-Type", "").lower().startswith(("text/html", "text/plain")):
                raise GateError("Citation URL did not return a text page")
            data = response.read(MAX_SOURCE + 1)
            if len(data) > MAX_SOURCE:
                raise GateError("Citation page is too large to verify")
        finally:
            conn.close()
    except (OSError, UnicodeError, ValueError, http.client.HTTPException) as exc:
        if isinstance(exc, GateError):
            raise
        raise GateError("Cannot independently fetch company citation") from exc
    decoded = data.decode("utf-8", "replace")
    if "html" in response.getheader("Content-Type", "").lower():
        parser = _PageText()
        parser.feed(decoded)
        return " ".join(parser.parts)
    return decoded


def company_findings(document, employer, citations, *, source_fetcher=fetch_public_source):
    findings, verified = [], []
    facts = [line.strip() for line in document.splitlines() if _company_fact(line, employer)]
    for i, source in enumerate(citations):
        if not isinstance(source, dict) or not all(isinstance(source.get(k), str) and source[k].strip() for k in ("claim", "url")):
            findings.append(f"Company citation #{i + 1} needs a claim and HTTPS URL")
            continue
        claim, url = source["claim"].strip(), source["url"].strip()
        if not has_term(normalized(document), claim):
            findings.append(f"Company citation #{i + 1} describes a claim absent from the draft")
            continue
        try:
            page = source_fetcher(url)
            if not has_term(normalized(page), claim):
                findings.append(f"Company claim is not independently confirmed by {url}")
            else:
                verified.append(claim)
        except GateError as exc:
            findings.append(f"Company citation verification failed: {exc}")
    public_quotes = [normalized(claim) for claim in verified]
    for fact in facts:
        if not _statement_is_verified(fact, public_quotes):
            findings.append("Unsourced employer-specific statement: " + fact[:120])
    return list(dict.fromkeys(findings)), "\n".join(verified)


def _cover_without_dateline(cover):
    """A standalone mailing date in the letterhead is not a biographical claim."""
    lines = cover.splitlines()
    for index, line in enumerate(lines[:8]):
        text = line.strip()
        if text.casefold().startswith("dear "):
            break
        try:
            datetime.strptime(text, "%B %d, %Y")
        except ValueError:
            continue
        del lines[index]
        break
    return "\n".join(lines)


def audit(profile, cv_pdf, *, cover_pdf=b"", keywords, claims=(), company_citations=(),
          employer="", visual_headings=(), source_fetcher=fetch_public_source):
    """Return a report. Every download must call this on the final uploaded bytes."""
    errors = []
    if not profile or not profile.get("full_name"):
        errors.append("Save and confirm a master profile before exporting")
    try:
        cv = pdf_text(cv_pdf, "CV")
        cover = pdf_text(cover_pdf, "Cover letter") if cover_pdf else ""
    except GateError as exc:
        return {"allowed": False, "errors": [str(exc)], "coverage": None}
    if not has_term(normalized(cv), profile.get("full_name", "")):
        errors.append("Final CV is missing the confirmed candidate name")
    if profile.get("email") and not has_term(normalized(cv), profile["email"]):
        errors.append("Final CV is missing the confirmed contact email")
    if "\x00" in cv or "\x00" in cover:
        errors.append("Invalid character in PDF text layer")
    order = section_order(cv, list(visual_headings))
    if order:
        errors.append(order)
    if not isinstance(keywords, list) or not keywords:
        errors.append("Provide posting keywords with priorities before export")
        coverage = None
    else:
        try:
            # Validate untrusted keyword JSON before computing any score.
            if len(keywords) > 150 or any(
                not isinstance(k, dict) or not isinstance(k.get("term"), str)
                or not k["term"].strip() or len(k["term"]) > 140
                or k.get("priority") not in ("required", "preferred")
                or not isinstance(k.get("aliases", []), list)
                or len(k.get("aliases", [])) > 20
                or any(not isinstance(a, str) or not a.strip() or len(a) > 140 for a in k.get("aliases", []))
                for k in keywords
            ) or len({normalized(k["term"]) for k in keywords}) != len(keywords):
                raise GateError("Invalid keyword list or priorities")
            coverage = evaluate(cv, keywords, profile_text(profile))
        except (KeyError, TypeError, ValueError, GateError):
            coverage = None
            errors.append("Invalid keyword list or priorities")
    content = cv + "\n" + cover
    company_errors, independently_verified = company_findings(content, employer, company_citations, source_fetcher=source_fetcher)
    errors.extend(company_errors)
    candidate_text = cv + "\n" + _cover_without_dateline(cover)
    errors.extend(candidate_findings(candidate_text, profile, claims, independently_verified, employer))
    if cover and not employer:
        errors.append("State the target employer to check cover letter company claims")
    verified_sources = [
        {"url": source["url"], "claim": source["claim"]}
        for source in company_citations
        if isinstance(source, dict) and isinstance(source.get("claim"), str)
        and source["claim"] in independently_verified
    ]
    return {"allowed": not errors, "errors": list(dict.fromkeys(errors)), "coverage": coverage,
            "verified_sources": verified_sources,
            "verified_pdf_pages": True,
            "pdf_sha256": hashlib.sha256(len(cv_pdf).to_bytes(8, "big") + cv_pdf + len(cover_pdf).to_bytes(8, "big") + cover_pdf).hexdigest(),
            "note": "Checks exact extracted text, declared evidence, high-risk numeric/degree/employer claims, "
                    "and nominated section order; visual geometry and unexpressed semantic claims still require human review."}
