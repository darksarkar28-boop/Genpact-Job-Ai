#!/usr/bin/env python3
"""Report literal ATS term coverage of a CV's extracted text layer.

The results are lexical evidence only, not judgments about candidate capability.
Aliases must be explicitly supplied by the caller; no term is invented and a
missing term is never silently converted into a claimed skill.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from tools.verify_pdf import normalize_text
except ModuleNotFoundError:  # python tools/ats_coverage.py from the repo root
    from verify_pdf import normalize_text


class CoverageError(ValueError):
    """Malformed keywords or unreadable input."""


def normalized(value: str) -> str:
    return normalize_text(value).casefold()


def has_term(text: str, term: str) -> bool:
    """Match a full term or phrase, not substrings (Go != Django)."""
    needle = normalized(term)
    if not needle:
        return False
    # A language suffix is part of its name: C, C++ and C# are distinct terms.
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w|\+\+|#)", text) is not None


def load_keywords(path: Path) -> list[dict]:
    try:
        if path.stat().st_size > 262_144:
            raise CoverageError("keyword file exceeds 256 KiB")
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageError(f"cannot read keyword JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("keywords"), list):
        raise CoverageError("expected an object with a 'keywords' list")
    items = doc["keywords"]
    if not 1 <= len(items) <= 150:
        raise CoverageError("expected 1 to 150 keywords")
    seen = set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise CoverageError(f"keyword #{i} must be an object")
        term = item.get("term")
        priority = item.get("priority")
        aliases = item.get("aliases", [])
        if not isinstance(term, str) or not term.strip() or len(term) > 140 or "\n" in term:
            raise CoverageError(f"keyword #{i} requires a nonempty, single-line term (max 140 chars)")
        if priority not in ("required", "preferred"):
            raise CoverageError(f"keyword #{i} priority must be 'required' or 'preferred'")
        if not isinstance(aliases, list) or len(aliases) > 20 or any(
            not isinstance(a, str) or not a.strip() or len(a) > 140 or "\n" in a for a in aliases
        ):
            raise CoverageError(f"keyword #{i} has invalid aliases")
        key = normalized(term)
        if key in seen:
            raise CoverageError(f"duplicate keyword: {term!r}")
        seen.add(key)
    return items


def evaluate(cv_text: str, keywords: list[dict], profile_text: str | None = None) -> dict:
    cv = normalized(cv_text)
    profile = normalized(profile_text) if profile_text is not None else None
    results = []
    summary = {priority: {"covered": 0, "synonym_only": 0, "missing": 0, "total": 0}
               for priority in ("required", "preferred")}
    for item in keywords:
        term, priority = item["term"], item["priority"]
        aliases = item.get("aliases", [])
        bucket = summary[priority]
        bucket["total"] += 1
        alias_match = next((alias for alias in aliases if has_term(cv, alias)), None)
        if has_term(cv, term):
            status, matched = "covered", term
            bucket["covered"] += 1
        elif alias_match is not None:
            status, matched = "synonym-only", alias_match
            bucket["synonym_only"] += 1
        else:
            profile_match = profile is not None and any(
                has_term(profile, t) for t in (term, *aliases)
            )
            status = "missing (have it)" if profile_match else "missing (unverified)"
            matched = None
            bucket["missing"] += 1
        results.append({"term": term, "priority": priority, "status": status,
                        "matched_term": matched})
    for bucket in summary.values():
        bucket["coverage_percent"] = round(100 * bucket["covered"] / bucket["total"], 1) if bucket["total"] else None
    return {"keywords": results, "summary": summary,
            "note": "Literal CV matches only; aliases do not count as exact ATS hits. "
                    "Profile-text matches are suggestions, not verified evidence. "
                    "Missing/unverified never proves a candidate lacks a skill."}


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > 5_242_880:
            raise CoverageError(f"input text exceeds 5 MiB: {path}")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CoverageError(f"cannot read {path}: {exc}") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cv-text", type=Path, required=True, help="CV text extracted by verify_pdf.py")
    parser.add_argument("--terms", type=Path, required=True, help="JSON object containing keywords")
    parser.add_argument("--profile-text", type=Path, action="append", default=[],
                        help="confirmed profile text (repeatable); used only to suggest missing (have it)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        terms = load_keywords(args.terms)
        cv = _read_text(args.cv_text)
        if not normalized(cv):
            raise CoverageError("CV text layer is empty; cannot measure ATS coverage")
        profile = "\n".join(_read_text(path) for path in args.profile_text) if args.profile_text else None
        report = evaluate(cv, terms, profile)
    except CoverageError as exc:
        print(f"ATS coverage error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Keyword | Priority | Status | Match")
        print("--- | --- | --- | ---")
        for record in report["keywords"]:
            def cell(value):
                return str(value or "-").replace("|", "\\|").replace("\r", " ")
            print(" | ".join(cell(record[name]) for name in ("term", "priority", "status", "matched_term")))
        for priority, values in report["summary"].items():
            percent = values["coverage_percent"]
            coverage = f"{percent}%" if percent is not None else "N/A"
            print(f"{priority}: exact {values['covered']}/{values['total']}, "
                  f"synonym-only {values['synonym_only']}, missing {values['missing']}, "
                  f"literal coverage {coverage}")
        print(report["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
