import unittest
from urllib.error import HTTPError

from tools.job_feeds import PARTNER
from tools.partner_job_feeds import PartnerFeed, ingest_partner


class PartnerTests(unittest.TestCase):
    def test_each_licensed_provider(self):
        for provider in PARTNER:
            with self.subTest(provider=provider):
                feed = PartnerFeed(provider, "https://relay.example/jobs", "fake-token")
                def fetch(url, headers):
                    self.assertEqual(headers["Authorization"], "Bearer fake-token")
                    return {"version": 1, "provider": provider, "jobs": [
                        {"source_id": "job-1", "employer": "Acme", "title": "Python developer",
                         "location": "Bengaluru", "application_url": "https://example.org/job/1",
                         "posted_at": "2026-11-24T09:00:00Z", "region": "india",
                         "description": "Python and Django"}]}
                result = ingest_partner(feed, fetch=fetch)
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.jobs[0].provider, provider)
                self.assertEqual(result.jobs[0].location, "Bengaluru")
                self.assertEqual(result.jobs[0].region, "india")

    def test_pagination_rate_limit_and_invalid_row(self):
        feed = PartnerFeed("naukri", "https://relay.example/jobs", "fake")
        calls, sleeps = [], []
        def fetch(url, headers):
            calls.append(url)
            if len(calls) == 1:
                raise HTTPError(url, 429, "rate limit", {"Retry-After": "3"}, None)
            if "?" not in url:
                return {"version": 1, "provider": "naukri", "jobs": [{}], "next_cursor": "a/b"}
            return {"version": 1, "provider": "naukri", "jobs": [
                {"source_id": 4, "employer": "A", "title": "Engineer",
                 "application_url": "https://a.example/4"}]}
        result = ingest_partner(feed, fetch=fetch, sleep=sleeps.append)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(result.jobs[0].region, "india")
        self.assertEqual(sleeps, [3])
        self.assertIn("cursor=a%2Fb", calls[-1])

    def test_cursor_loop_mismatch_and_bad_configuration(self):
        feed = PartnerFeed("indeed", "https://relay.example/jobs", "fake")
        self.assertEqual(ingest_partner(feed, fetch=lambda *_: {
            "version": 1, "provider": "linkedin", "jobs": []}).status, "unavailable")
        self.assertEqual(ingest_partner(feed, fetch=lambda *_: {
            "version": 1, "provider": "indeed", "jobs": [], "next_cursor": "same"}).status, "unavailable")
        for url in ("http://relay.example", "https://u:p@relay.example", "https://relay.example?a=b"):
            with self.assertRaises(ValueError):
                PartnerFeed("indeed", url, "fake")
        self.assertNotIn("fake", repr(feed))
