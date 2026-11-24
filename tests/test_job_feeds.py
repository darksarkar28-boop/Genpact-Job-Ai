import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError

from tools.job_feeds import Board, access_status, collect, ingest, normalize


class JobFeedTests(unittest.TestCase):
    def setUp(self):
        self.greenhouse = Board("greenhouse", "acme", "Acme", "india")
        self.lever = Board("lever", "acme", "Acme")
        self.ashby = Board("ashby", "acme", "Acme")
        self.row = {"id": 1, "title": "Engineer", "location": {"name": "Bengaluru"},
                    "absolute_url": "https://jobs.example/1?utm_source=x",
                    "updated_at": "2026-10-10T10:00:00Z"}

    def test_greenhouse_fields_and_cli_key(self):
        job = normalize(self.greenhouse, self.row)
        self.assertEqual(job.identity, ("greenhouse", "acme", "1"))
        self.assertEqual(job.location, "Bengaluru")
        self.assertEqual(job.region, "india")
        self.assertEqual(job.url, "https://jobs.example/1")
        self.assertEqual(job.legacy_key, "acme_engineer")
        self.assertIsNone(job.posted_at)

    def test_lever_fields_and_ashby_missing_id(self):
        lever = normalize(self.lever, {"id": "L1", "text": "Developer",
            "categories": {"location": "Pune"}, "applyUrl": "https://jobs.example/2",
            "createdAt": 1760000000000, "descriptionPlain": "Python"})
        self.assertEqual(lever.location, "Pune")
        self.assertEqual(lever.description, "Python")
        self.assertEqual(lever.posted_at.tzinfo, timezone.utc)
        ashby = normalize(self.ashby, {"title": "Engineer", "location": "Delhi",
            "jobUrl": "https://jobs.example/3", "applyUrl": "https://jobs.example/3/apply",
            "publishedAt": "2026-10-10T10:00:00Z"})
        self.assertEqual(ashby.source_id, "https://jobs.example/3")
        self.assertEqual(ashby.url, "https://jobs.example/3/apply")

    def test_unknown_partner_status_and_board_validation(self):
        for provider in ("linkedin", "indeed", "naukri", "hirist", "instahyre", "internshala"):
            self.assertEqual(access_status(provider), "authorized_integration_required")
            with self.assertRaises(ValueError):
                Board(provider, "acme", "Acme")
        with self.assertRaises(ValueError):
            Board("lever", "../other", "Acme")

    def test_url_identity_keeps_query_and_rejects_bad_scheme(self):
        job = normalize(self.greenhouse, dict(self.row, absolute_url="https://jobs.example/?id=9&gh_src=foo"))
        self.assertEqual(job.url, "https://jobs.example/?id=9")
        for url in ("javascript:alert(1)", "http://jobs.example/1", "https://u:p@jobs.example/1"):
            with self.assertRaises(ValueError):
                normalize(self.greenhouse, dict(self.row, absolute_url=url))

    def test_dedup_expiry_and_separate_openings(self):
        rows = [self.row, dict(self.row, id=2, absolute_url="https://jobs.example/2"),
                dict(self.row, id=3, absolute_url="https://jobs.example/3", expiresAt="2020-01-01"),
                dict(self.row, id=4, absolute_url="https://jobs.example/1?utm_campaign=duplicate")]
        jobs, results = collect([self.greenhouse], fetch=lambda _: {"jobs": rows},
                                now=datetime(2026, 11, 24, tzinfo=timezone.utc))
        self.assertEqual({job.source_id for job in jobs}, {"1", "2"})
        self.assertEqual(results[0].status, "ok")

    def test_invalid_row_does_not_abort_and_provider_failure_isolated(self):
        def fetch(url):
            if "greenhouse" in url:
                return {"jobs": [self.row, {}, dict(self.row, updated_at="not-a-date")]}
            raise OSError("offline")
        jobs, results = collect([self.greenhouse, self.lever], fetch=fetch)
        self.assertEqual(len(jobs), 1)
        self.assertEqual([r.status for r in results], ["partial", "unavailable"])
        self.assertEqual(results[0].invalid_count, 2)

    def test_lever_pagination_and_rate_limit(self):
        urls, sleeps = [], []
        def fetch(url):
            urls.append(url)
            if len(urls) == 1:
                raise HTTPError(url, 429, "rate limited", {"Retry-After": "2"}, None)
            if "skip=0" in url:
                return [{"id": str(i), "text": "Developer", "applyUrl": f"https://jobs.example/{i}"} for i in range(100)]
            return [{"id": "100", "text": "Developer", "applyUrl": "https://jobs.example/100"}]
        result = ingest(self.lever, fetch=fetch, sleep=sleeps.append)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.jobs), 101)
        self.assertEqual(sleeps, [2])
        self.assertIn("skip=100", urls[-1])

    def test_page_limit_visible_and_unlisted_ashby_ignored(self):
        row = {"id": "x", "text": "Engineer", "applyUrl": "https://jobs.example/x"}
        result = ingest(self.lever, fetch=lambda _: [row] * 100, max_pages=1)
        self.assertEqual(result.status, "partial")
        result = ingest(self.ashby, fetch=lambda _: {"jobs": [{"isListed": False}]})
        self.assertEqual(result.jobs, [])
        self.assertEqual(result.status, "ok")
