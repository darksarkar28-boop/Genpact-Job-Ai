import io
import json
import unittest
from urllib.error import HTTPError

from tools.model_router import Model, Result, Router, RoutingError
from tools.model_service import CompatibleProvider, configured_router
from tools.routed_workflows import run


class RoutingIntegrationTests(unittest.TestCase):
    def test_real_adapter_with_transport_double(self):
        events, requests = [], []

        class Transport:
            def open(self, request, timeout):
                body = json.loads(request.data)
                requests.append((body["model"], timeout))
                prompt = body["messages"][0]["content"]
                if "potentially duplicate" in prompt:
                    value = []
                elif "Score each posting" in prompt:
                    value = [{"key": "a", "scores": dict.fromkeys(("technical", "experience", "behavioral", "career"), 80),
                              "location_verdict": "PASS", "language_gate": "PASS", "eligibility_gate": "FLAG",
                              "strengths": [], "gaps": []}]
                elif "Audit the draft" in prompt:
                    value = {"cv": "Python", "cover_letter": "Application", "findings": []}
                elif "Write candidate-grounded" in prompt:
                    value = {"cv": "Python", "cover_letter": "Application"}
                else:
                    value = {"skills": ["Python"]}
                return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                                             "usage": {"prompt_tokens": 20, "completion_tokens": 10}}).encode())

        config = {"providers": {"p": {"endpoint": "https://example.com/v1/chat/completions", "credential_env": "KEY"}},
                  "fast": [{"provider": "p", "name": "fast", "input_rate": 1, "output_rate": 2}],
                  "frontier": [{"provider": "p", "name": "frontier", "input_rate": 10, "output_rate": 20}],
                  "budgets_usd": dict.fromkeys(("extract", "deduplicate", "rank", "draft", "review"), 1),
                  "timeouts_seconds": dict.fromkeys(("extract", "deduplicate", "rank", "draft", "review"), 5)}
        router = configured_router(config, {"KEY": "fixture"}, lambda endpoint, credential: CompatibleProvider(endpoint, credential, Transport()), events.append)
        def route(workload, prompt):
            return router.call(workload, prompt, len(prompt.encode("utf-8")) + 256).text
        job = {"key": "a", "text": "Python developer"}
        profile = {"skills": ["Python"]}
        run("extract", {"text": "Example Person\nSkills: Python"}, route=route)
        run("deduplicate", {"jobs": [job, {"key": "b", "text": "SQL developer"}]}, route=route)
        run("rank", {"profile": profile, "jobs": [job]}, route=route)
        run("apply", {"profile": profile, "job": job}, route=route)
        self.assertEqual([name for name, _ in requests], ["fast", "fast", "fast", "frontier", "frontier"])
        self.assertTrue(all(0 < timeout <= 5 for _, timeout in requests))
        self.assertEqual([event["workload"] for event in events], ["extract", "deduplicate", "rank", "draft", "review"])
        self.assertNotIn("Example Person", repr(events))
        self.assertNotIn("fixture", repr(events))

    def test_total_workload_deadline_prevents_fallback(self):
        now = [0]
        calls = []
        def fail(*args, **kwargs):
            calls.append(kwargs["timeout"])
            now[0] = 6
            raise TimeoutError()
        def unexpected(*args, **kwargs):
            self.fail("fallback after deadline")
        fast = [Model("a", "fast", 1, 1), Model("b", "fallback", 1, 1)]
        router = Router(fast, [], {"a": fail, "b": unexpected}, {"rank": 1},
                        timeouts={"rank": 5}, clock=lambda: now[0])
        with self.assertRaises(RoutingError):
            router.call("rank", "text", 5)
        self.assertEqual(calls, [5])
