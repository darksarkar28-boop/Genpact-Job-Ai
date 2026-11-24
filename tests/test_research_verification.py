import io
import json
import unittest

from tools.research_verification import verify_sources


class VerificationTests(unittest.TestCase):
    def test_only_approved_sources_are_refetched(self):
        allowed = "https://example.com/about"
        unapproved = "https://example.com/private"
        calls = []

        class Transport:
            def open(self, request, timeout):
                calls.append(request.full_url)
                return io.BytesIO(b"<p>Public company information from its original website.</p>")

        leads = {"sources": [{"url": allowed, "summary": "Public company information"},
                             {"url": unapproved, "summary": "Private data is not public"}]}
        env = {"JOB_AI_PUBLIC_SOURCE_URLS": json.dumps({"IN-MCA:EXAMPLE": [allowed]})}
        result = verify_sources("IN-MCA:EXAMPLE", leads, env, Transport())
        self.assertEqual(calls, [allowed])
        self.assertEqual([r["status"] for r in result], ["literal_excerpt_found", "manual_verification_required"])

    def test_hidden_excerpt_not_verified(self):
        url = "https://example.com/about"
        class Transport:
            def open(self, request, timeout):
                return io.BytesIO(b"<script>Invented company statement</script><p>Different statement</p>")
        leads = {"sources": [{"url": url, "summary": "Invented company statement"}]}
        env = {"JOB_AI_PUBLIC_SOURCE_URLS": json.dumps({"IN-MCA:EXAMPLE": [url]})}
        self.assertEqual(verify_sources("IN-MCA:EXAMPLE", leads, env, Transport())[0]["status"], "excerpt_not_confirmed")
