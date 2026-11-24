import json
import tempfile
import unittest

from tools.model_router import RoutingError
from tools.routed_workflows import application, duplicates, rank
from tools.shared_research_cache import ResearchCache


class WorkflowTests(unittest.TestCase):
    def test_batch_rank_and_veto(self):
        jobs = [{"key": "a", "text": "Python developer"}, {"key": "b", "text": "SQL developer"}]
        def route(workload, prompt):
            self.assertEqual(workload, "rank")
            return json.dumps([{"key": key, "scores": dict.fromkeys(("technical", "experience", "behavioral", "career"), score),
                                "location_verdict": "PASS", "language_gate": "PASS", "eligibility_gate": gate,
                                "strengths": [], "gaps": []}
                               for key, score, gate in [("a", 99, "FAIL"), ("b", 80, "PASS")]])
        rows = rank({"skills": ["Python"]}, jobs, route)
        self.assertEqual([row["key"] for row in rows], ["b", "a"])
        self.assertTrue(rows[1]["excluded"])
        with self.assertRaises(RoutingError):
            rank({"skills": ["Python"]}, jobs, lambda *a: "[]")

    def test_duplicate_suggestions_are_separate(self):
        jobs = [{"key": "a", "text": "Python developer"}, {"key": "b", "text": "Python developer"},
                {"key": "c", "text": "Developer, Python"}]
        def route(workload, prompt):
            self.assertEqual(workload, "deduplicate")
            return '[["a","c"]]'
        self.assertEqual(duplicates(jobs, route), {"exact": [["a", "b"]], "suggestions": [["a", "c"]]})

    def test_application_cache_is_lead_not_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            from pathlib import Path
            cache = ResearchCache(Path(root) / "public_research.sqlite3")
            cache.get_or_refresh("IN-MCA:EXAMPLE", lambda key: [{"url": "https://example.com", "summary": "CACHE_ONLY"}])
            calls = []
            def route(workload, prompt):
                calls.append(workload)
                self.assertNotIn("CACHE_ONLY", prompt)
                value = {"cv": "Python", "cover_letter": "Application"}
                if workload == "review":
                    value["findings"] = []
                return json.dumps(value)
            result = application({"skills": ["Python"]}, {"key": "a", "text": "Python role"}, "IN-MCA:EXAMPLE", root, route)
            self.assertEqual(calls, ["draft", "review"])
            self.assertTrue(result["research_leads"]["cache_hit"])
            self.assertTrue(result["requires_company_verification"])
