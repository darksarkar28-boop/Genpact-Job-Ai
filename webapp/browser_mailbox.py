"""Start each mailbox connection from a signed-in session using a hosted browser."""
import os
import re
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright


class MailboxConnectionTests(StaticLiveServerTestCase):
    def setUp(self):
        self.password = "mailbox-browser-fixture-37"
        self.user = get_user_model().objects.create_user("mail_reader", password=self.password)

    def check_connection(self, provider, host, read_scopes):
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
            try:
                context = browser.new_context()
                origin = urlsplit(self.live_server_url).netloc

                def intercept(route):
                    if urlsplit(route.request.url).netloc == origin:
                        route.continue_()
                    else:
                        # Observe the authorization request without contacting a provider.
                        route.fulfill(status=200, content_type="text/plain", body="Fixture consent page")

                context.route("**/*", intercept)
                page = context.new_page()
                page.goto(self.live_server_url + reverse("login"))
                page.locator("[name=username]").fill(self.user.username)
                page.locator("[name=password]").fill(self.password)
                page.get_by_role("button", name="Sign in").click()
                page.get_by_role("link", name="Applications", exact=True).click()

                def control(name):
                    return page.get_by_role("link", name=name).or_(page.get_by_role("button", name=name)).first

                connect = control(provider)
                if not connect.is_visible():
                    settings = control(re.compile(r"mailbox|email connections|integrations|account settings", re.I))
                    if settings.is_visible():
                        settings.click()
                expect(connect, "A signed-in candidate needs a way to connect this mailbox").to_be_visible(timeout=3000)
                with context.expect_event("request", predicate=lambda req: urlsplit(req.url).hostname == host) as consent:
                    connect.click()
                url = urlsplit(consent.value.url)
                params = parse_qs(url.query)
                self.assertEqual(url.scheme, "https")
                self.assertTrue(params.get("client_id"))
                self.assertTrue(params.get("state"))
                self.assertEqual(params.get("response_type"), ["code"])
                scopes = set(params.get("scope", [""])[0].lower().split())
                self.assertTrue(scopes & read_scopes, "A mail read scope is required")
                self.assertFalse(any("send" in scope or "write" in scope or "modify" in scope for scope in scopes))
            finally:
                browser.close()

    def test_candidate_can_start_gmail_read_consent(self):
        self.check_connection(re.compile(r"gmail|google", re.I), "accounts.google.com", {
            "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.metadata",
        })

    def test_candidate_can_start_outlook_read_consent(self):
        self.check_connection(re.compile(r"outlook|microsoft", re.I), "login.microsoftonline.com", {
            "mail.read", "mail.readbasic", "https://graph.microsoft.com/mail.read",
            "https://graph.microsoft.com/mail.readbasic",
        })
