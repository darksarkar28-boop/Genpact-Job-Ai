"""Focused regression tests for issue #6's ATS coverage reporting."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.ats_coverage import CoverageError, evaluate, has_term, load_keywords, main, normalized


class MatchTests(unittest.TestCase):
    def test_word_boundaries_prevent_go_from_matching_django(self):
        self.assertFalse(has_term(normalized("Django developer at Google"), "Go"))
        self.assertTrue(has_term(normalized("Go and Django developer"), "Go"))

    def test_multiterm_and_casefold_and_curly_quotes(self):
        self.assertTrue(has_term(normalized("PROFESSIONAL\n EXPERIENCE; Master's"), "professional experience"))
        self.assertTrue(has_term(normalized("Master’s degree"), "Master's degree"))

    def test_regex_characters_are_literal(self):
        self.assertTrue(has_term(normalized("C++ and C#"), "C++"))
        self.assertFalse(has_term(normalized("C followed by Java"), "C++"))

    def test_language_suffixes_stay_with_their_term(self):
        text = normalized("C++ and C# developer using Python+SQL")
        self.assertFalse(has_term(text, "C"))
        self.assertTrue(has_term(text, "C#"))
        self.assertTrue(has_term(text, "Python"))
        self.assertTrue(has_term(normalized("C/C++ developer"), "C"))


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.terms = [
            {"term": "Python", "priority": "required"},
            {"term": "Continuous integration", "priority": "required", "aliases": ["CI"]},
            {"term": "Kubernetes", "priority": "required"},
            {"term": "Docker", "priority": "preferred"},
        ]

    def test_status_and_exact_denominator(self):
        report = evaluate("Python, CI, Docker", self.terms, "Kubernetes projects")
        self.assertEqual([r["status"] for r in report["keywords"]],
                         ["covered", "synonym-only", "missing (have it)", "covered"])
        self.assertEqual(report["summary"]["required"],
                         {"covered": 1, "synonym_only": 1, "missing": 1, "total": 3, "coverage_percent": 33.3})
        self.assertEqual(report["summary"]["preferred"]["coverage_percent"], 100.0)

    def test_missing_skill_without_confirmed_evidence_not_called_gap(self):
        report = evaluate("Python", self.terms)
        self.assertEqual(report["keywords"][2]["status"], "missing (unverified)")

    def test_zero_preferred_denominator_not_divide_by_zero(self):
        report = evaluate("Python", self.terms[:1])
        self.assertIsNone(report["summary"]["preferred"]["coverage_percent"])


class CLITests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.cv = self.root / "cv.txt"
        self.cv.write_text("Python SQL and CI", encoding="utf-8")
        self.terms = self.root / "terms.json"
        self.terms.write_text(json.dumps({"keywords": [
            {"term": "Python", "priority": "required"},
            {"term": "Continuous integration", "priority": "preferred", "aliases": ["CI"]},
        ]}), encoding="utf-8")

    def test_json_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--cv-text", str(self.cv), "--terms", str(self.terms), "--json"])
        self.assertEqual(code, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["keywords"][1]["status"], "synonym-only")

    def test_invalid_priority_fails(self):
        self.terms.write_text(json.dumps({"keywords": [{"term": "SQL", "priority": "unknown"}]}))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["--cv-text", str(self.cv), "--terms", str(self.terms)])
        self.assertEqual(code, 1)
        self.assertIn("priority", stderr.getvalue())

    def test_text_report_for_empty_priority_group(self):
        self.terms.write_text(json.dumps({"keywords": [{"term": "Python", "priority": "required"}]}))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--cv-text", str(self.cv), "--terms", str(self.terms)])
        self.assertEqual(code, 0)
        self.assertIn("preferred: exact 0/0, synonym-only 0, missing 0, literal coverage N/A", stdout.getvalue())

    def test_empty_cv_fails_closed(self):
        self.cv.write_text("  \n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["--cv-text", str(self.cv), "--terms", str(self.terms)])
        self.assertEqual(code, 1)
        self.assertIn("empty", stderr.getvalue())

    def test_duplicate_term_rejected(self):
        self.terms.write_text(json.dumps({"keywords": [
            {"term": "Python", "priority": "required"},
            {"term": "PYTHON", "priority": "preferred"},
        ]}))
        with self.assertRaisesRegex(CoverageError, "duplicate"):
            load_keywords(self.terms)


if __name__ == "__main__":
    unittest.main()
