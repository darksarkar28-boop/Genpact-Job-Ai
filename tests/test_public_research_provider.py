import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.public_research_provider import configured_feed, PublicFeedUnavailable
from tools.routed_workflows import research, run
from tools.shared_research_cache import ResearchCache


class PublicFeedTests(unittest.TestCase):
    def test_refresh_coalesces_and_isolates_registry_ids(self):
        calls = []
        class Transport:
            def open(self, request, timeout):
                calls.append(request.full_url)
                return io.BytesIO(json.dumps({"employer_id": "IN-MCA:ABC",
                    "sources": [{"url": "https://example.com/about", "summary": "Public facts"}]}).encode())
        fetch = configured_feed({"JOB_AI_PUBLIC_RESEARCH_FEEDS": json.dumps(
            {"IN-MCA:ABC": "https://example.com/feed"})}, Transport())
        with tempfile.TemporaryDirectory() as root, patch("tools.routed_workflows.configured_feed", return_value=fetch):
            self.assertFalse(research("IN-MCA:ABC", root)["cache_hit"])
            self.assertTrue(research("IN-MCA:ABC", root)["cache_hit"])
            self.assertIsNone(research("US:ABC", root))
            self.assertEqual(calls, ["https://example.com/feed"])

    def test_reject_wrong_employer(self):
        class Transport:
            def open(self, request, timeout):
                return io.BytesIO(b'{"employer_id":"US:ABC","sources":[]}')
        fetch = configured_feed({"JOB_AI_PUBLIC_RESEARCH_FEEDS": '{"IN-MCA:ABC":"https://example.com/feed"}'}, Transport())
        with self.assertRaises(PublicFeedUnavailable):
            fetch("IN-MCA:ABC")

    def test_extract_route(self):
        calls = []
        def route(workload, prompt):
            calls.append(workload)
            return '{"skills":["Python","invented"]}'
        result = run("extract", {"text": "Example Person\nSkills: Python"}, route=route)
        self.assertEqual(calls, ["extract"])
        self.assertIn("Python", result["skills"])
        self.assertNotIn("invented", result["skills"])
