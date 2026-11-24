import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.model_router import Model, Result, Router, RoutingError
from tools.shared_research_cache import ResearchCache


class RouterTests(unittest.TestCase):
    def test_tiers_and_usage(self):
        events = []
        def adapter(name, prompt, **limits):
            self.assertEqual(limits, {"timeout": 30, "max_output": 10})
            return Result(name, 2, 1)
        fast = Model("p", "cheap", 1, 1, max_output=10)
        strong = Model("p", "strong", 10, 10, max_output=10)
        workloads = ("extract", "deduplicate", "rank", "draft", "review")
        router = Router([fast], [strong], {"p": adapter}, dict.fromkeys(workloads, 1), events.append)
        for workload in workloads:
            expected = "strong" if workload in ("draft", "review") else "cheap"
            self.assertEqual(router.call(workload, "sample", 5).text, expected)
        self.assertNotIn("sample", repr(events))
        self.assertEqual(len(events), 5)

    def test_budget_and_fallback(self):
        def fail(*args, **kwargs):
            raise TimeoutError()
        def ok(*args, **kwargs):
            return Result("ok", 1, 1)
        a = Model("a", "one", 1, 1, max_output=10)
        b = Model("b", "two", 1, 1, max_output=10)
        for budget, succeeds in [(0, False), (0.000015, False), (0.0001, True)]:
            router = Router([a, b], [], {"a": fail, "b": ok}, {"rank": budget})
            if succeeds:
                self.assertEqual(router.call("rank", "x", 5).text, "ok")
            else:
                with self.assertRaises(RoutingError):
                    router.call("rank", "x", 5)

    def test_invalid_provider_usage(self):
        model = Model("p", "cheap", 1, 1, max_output=10)
        router = Router([model], [], {"p": lambda *a, **k: Result("x", 6, 1)}, {"rank": 1})
        with self.assertRaises(RoutingError):
            router.call("rank", "x", 5)


class CacheTests(unittest.TestCase):
    def test_reuse_expiry_and_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cache.db"
            now = [100]
            one = ResearchCache(path, ttl=10, clock=lambda: now[0])
            two = ResearchCache(path, ttl=10, clock=lambda: now[0])
            calls = []
            def fetch(key):
                calls.append(key)
                return [{"url": "https://example.com", "summary": key}]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda cache: cache.get_or_refresh("IN:123", fetch), [one, two]))
            self.assertEqual(calls, ["IN:123"])
            self.assertEqual(sum(r["cache_hit"] for r in results), 1)
            self.assertTrue(all(r["requires_verification"] for r in results))
            self.assertFalse(two.get_or_refresh("US:123", fetch)["cache_hit"])
            now[0] += 10
            self.assertFalse(one.get_or_refresh("IN:123", fetch)["cache_hit"])

    def test_invalid_source_and_retry(self):
        with tempfile.TemporaryDirectory() as root:
            cache = ResearchCache(Path(root) / "cache.db")
            with self.assertRaises(ValueError):
                cache.get_or_refresh("IN:123", lambda key: [{"url": "http://example.com", "summary": "x"}])
            result = cache.get_or_refresh("IN:123", lambda key: [{"url": "https://example.com", "summary": "public"}])
            self.assertFalse(result["cache_hit"])
