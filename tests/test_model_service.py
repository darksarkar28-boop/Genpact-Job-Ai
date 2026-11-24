import json
import unittest

from tools.model_router import Result, RoutingError
from tools.model_service import configured_router
from webapp.ai_extract import extract_draft


class ModelServiceTests(unittest.TestCase):
    def config(self):
        return {"providers": {"a": {"endpoint": "https://example.com/v1/chat/completions",
                                    "credential_env": "TEST_KEY"}},
                "fast": [{"provider": "a", "name": "cheap", "input_rate": 1, "output_rate": 2}],
                "frontier": [{"provider": "a", "name": "frontier", "input_rate": 5, "output_rate": 10}],
                "budgets_usd": dict.fromkeys(("extract", "rank", "deduplicate", "draft", "review"), 1)}

    def test_configured_tiers(self):
        def provider(endpoint, credential):
            return lambda name, prompt, **kw: Result(name, 1, 1)
        events = []
        router = configured_router(self.config(), {"TEST_KEY": "fixture"}, provider, events.append)
        self.assertEqual(router.call("extract", "text", 5).text, "cheap")
        self.assertEqual(router.call("draft", "text", 5).text, "frontier")
        self.assertEqual(len(events), 2)
        self.assertNotIn("fixture", repr(events))

    def test_missing_credential(self):
        router = configured_router(self.config(), {})
        with self.assertRaises(RoutingError):
            router.call("rank", "text", 10)

    def test_same_model_cannot_cross_tiers(self):
        config = self.config()
        config["frontier"] = config["fast"]
        with self.assertRaises(ValueError):
            configured_router(config, {})

    def test_extract_literal_and_fallback(self):
        local = {"full_name": "Example Person", "skills": ["Python"]}
        text = "Example Person\nPython, SQL\nEngineer"
        def route(workload, prompt):
            self.assertEqual(workload, "extract")
            return json.dumps({"full_name": "Another Person", "headline": "Engineer", "skills": ["SQL", "COBOL"]})
        result = extract_draft(text, local, route)
        self.assertEqual(result["full_name"], "Example Person")
        self.assertEqual(result["headline"], "Engineer")
        self.assertEqual(result["skills"], ["Python", "SQL"])
        def fail(*args):
            raise RoutingError("unavailable")
        self.assertEqual(extract_draft(text, local, fail), local)
        self.assertEqual(extract_draft(text, local, lambda *a: "bad JSON"), local)
