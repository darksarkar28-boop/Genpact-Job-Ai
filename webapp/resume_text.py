"""Extract literal résumé fields. Leave unlabelled or ambiguous prose available for review."""
import re

from .profile_data import skill_list

SECTIONS = {
    "summary": ("summary", "profile", "professional summary", "about", "about me", "career objective"),
    "experience": ("experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "relevant experience", "internships"),
    "education": ("education", "academic background", "academic qualifications", "qualifications"),
    "skills": ("skills", "top skills", "technical skills", "key skills", "core competencies",
               "core skills", "skills and tools", "areas of expertise"),
    "contact": ("contact", "contact information", "contact details"),
    "other": ("languages", "certifications", "projects", "publications", "awards", "references",
              "volunteering", "volunteer experience", "honors and awards", "honors-awards"),
}
HEADINGS = {name: section for section, names in SECTIONS.items() for name in names}
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[^\W\d_]{2,}", re.UNICODE)
PHONE = re.compile(r"(?<![\w])\+?\(?\d[\d ().-]{5,}\d(?!\w)")
ROLE = re.compile(r"\b(engineer|developer|manager|analyst|designer|intern|director|lead|specialist|"
                  r"consultant|scientist|researcher|officer|associate|technician|coordinator|architect)\b", re.I)
DEGREE = re.compile(r"\b(bachelor|master|ph\.?d|degree|diploma|b\.?sc|m\.?sc|b\.?tech|mba)\b", re.I)
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
YEAR = r"(?:19|20)\d{2}"
DATE = rf"(?:(?:{MONTH}\s+|\d{{1,2}}/))?{YEAR}"
PERIOD = re.compile(rf"\b{DATE}\s*(?:[-–—]|\bto\b)\s*(?:{DATE}|present|current|now)\b", re.I)
BULLETS = "•●▪◦*"


def _heading(line):
    label, sep, rest = line.partition(":")
    return HEADINGS.get(label.strip().casefold()), rest.strip() if sep else ""


def _plausible_name(line):
    words = line.split()
    return (1 <= len(words) <= 6 and len(line) <= 80 and any(c.isalpha() for c in line)
            and all(c.isalpha() or c in " .'-’" for c in line)
            and not ROLE.search(line) and line.casefold() not in {"resume", "résumé", "curriculum vitae", "cv"})


def _identity_lines(lines):
    """LinkedIn PDFs can put Contact and Top Skills ahead of the main identity block."""
    main = next((i for i, line in enumerate(lines) if _heading(line)[0] in
                 {"summary", "experience", "education"}), len(lines))
    header = lines[:main]
    if not header:
        return []
    first_section = next((i for i, line in enumerate(header) if _heading(line)[0]), len(header))
    if first_section:
        return header[:first_section]
    # The main-column name/headline/location often directly precede Summary or Experience.
    for i in range(len(header) - 2, max(-1, len(header) - 6), -1):
        if i >= 0 and _plausible_name(header[i]) and not _heading(header[i])[0]:
            if ROLE.search(header[i + 1]):
                return header[i:]
    return []


def _strip_period(line, match):
    if not match:
        return line
    # LinkedIn puts a derived tenure in parentheses after the date range.
    tail = re.sub(r"^\s*\([^)]*\byear[^)]*\)|^\s*\([^)]*\bmonth[^)]*\)", "", line[match.end():], flags=re.I)
    return (line[:match.start()] + " " + tail).strip(" \t·|(),-–—")


def _looks_like_header(line):
    return len(line) < 120 and not line.startswith(tuple(BULLETS + "-")) and not line.endswith((".", ";"))


def _records(lines, section):
    if not lines:
        return []
    # A date range anchors the preceding title/institution. Keep bullet text, including dates
    # inside sentences, with its record. Text with no obvious boundary stays together.
    anchors = [(i, m) for i, line in enumerate(lines)
               if _looks_like_header(line) and (m := PERIOD.search(line))]
    starts = [0]
    for (prev, _), (index, match) in zip(anchors, anchors[1:]):
        start = index
        if not _strip_period(lines[index], match):
            while start > prev + 1 and index - start < 2 and _looks_like_header(lines[start - 1]):
                start -= 1
                if re.search(r"\s(?:at|\|)\s", lines[start], re.I):
                    break
        elif section == "education" and start > prev + 1 and DEGREE.search(lines[index]):
            start -= 1
        starts.append(start)
    return [_record(lines[start:end], section) for start, end in zip(starts, starts[1:] + [len(lines)])]


def _record(lines, section):
    headers, rest, dates = [], [], ""
    body = False
    for line in lines:
        match = None if body else PERIOD.search(line)
        text = _strip_period(line, match)
        if not body and len(headers) < 2 and text and _looks_like_header(text):
            headers.extend(re.split(r"\s+(?:\||at|@)\s+", text, maxsplit=1, flags=re.I))
        elif text:
            rest.append(text)
            body = True
        if match:
            dates = match.group()
            body = True
    a, b, *extra = headers + ["", ""]
    if section == "experience":
        if b and ROLE.search(b) and not ROLE.search(a):
            a, b = b, a
        row = {"title": a, "employer": b}
    else:
        if DEGREE.search(a) and not DEGREE.search(b):
            a, b = b, a
        row = {"school": a, "degree": b}
    return {**row, "dates": dates, "details": "\n".join(x for x in extra + rest if x)}


def parse_resume(text):
    lines = [" ".join(x.split()) for x in text.splitlines() if x.strip()]
    lines = [x for x in lines if not re.fullmatch(r"Page\s+\d+\s+(?:of|/)\s*\d+", x, re.I)]
    identity = _identity_lines(lines)
    candidate = {}
    mail = EMAIL.search(text)
    if mail:
        candidate["email"] = mail.group()
    for line in lines:
        for match in PHONE.finditer(line):
            if 7 <= sum(c.isdigit() for c in match.group()) <= 15 and not PERIOD.search(match.group()):
                candidate["phone"] = match.group().strip()
                break
        if "phone" in candidate:
            break
    for line in identity:
        label, sep, value = line.partition(":")
        field = {"name": "full_name", "full name": "full_name", "location": "location",
                 "headline": "headline", "address": "location"}.get(label.lower())
        if sep and field:
            candidate[field] = value.strip()
    name_index = next((i for i, line in enumerate(identity) if _plausible_name(line)), None)
    if name_index is not None:
        candidate.setdefault("full_name", identity[name_index])
        remaining = [line for line in identity[name_index + 1:] if not EMAIL.search(line)
                     and not PHONE.search(line) and ":" not in line and "|" not in line
                     and "linkedin.com/" not in line and "github.com/" not in line]
        if remaining and ROLE.search(remaining[0]):
            candidate.setdefault("headline", remaining.pop(0))
        if remaining and ("," in remaining[0] or remaining[0].endswith(" Area")):
            candidate.setdefault("location", remaining[0])
    groups = {key: [] for key in SECTIONS}
    active = "other"
    for line in lines:
        section, inline = _heading(line)
        if section:
            active = section
            if inline:
                groups[active].append(inline)
        elif active in {"experience", "education", "summary"} or line not in identity:
            groups[active].append(line)
    candidate["summary"] = "\n".join(groups["summary"])
    candidate["skills"] = skill_list("\n".join(x.strip(BULLETS + " ") for x in groups["skills"]))
    for section in ("experience", "education"):
        candidate[section] = _records(groups[section], section)
    return candidate
