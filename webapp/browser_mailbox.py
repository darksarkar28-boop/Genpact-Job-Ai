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



class MailboxReviewBrowser(StaticLiveServerTestCase):
    """Real browser, two authenticated sessions, fake provider transport only."""

    def setUp(self):
        from datetime import timedelta
        from unittest.mock import patch

        from cryptography.fernet import Fernet
        from django.utils import timezone

        from .mail_oauth import encrypt
        from .models import Application, MailConnection

        self.password = "mail-review-browser-fixture-72"
        self.user = get_user_model().objects.create_user("mail_review_browser", password=self.password)
        self.other_user = get_user_model().objects.create_user("mail_review_unrelated", password=self.password)
        self.env = patch.dict(os.environ, {"JOBWEB_MAIL_TOKEN_KEY": Fernet.generate_key().decode()})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.application = Application.objects.create(
            user=self.user, company="Example Labs", role="Data Engineer", status="applied",
        )
        for provider in ("google", "microsoft"):
            MailConnection.objects.create(
                user=self.user, provider=provider,
                access_token_ciphertext=encrypt("fixture-access-token"),
                refresh_token_ciphertext=encrypt("fixture-refresh-token"),
                expires_at=timezone.now() + timedelta(hours=1),
            )

    def open_board(self, context, username):
        page = context.new_page()
        page.goto(self.live_server_url + reverse("login"))
        page.locator("[name=username]").fill(username)
        page.locator("[name=password]").fill(self.password)
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("link", name="Applications", exact=True).click()
        return page

    def test_review_approve_dismiss_and_other_session_updates(self):
        from unittest.mock import patch

        from django.utils import timezone

        from .models import ApplicationStatusEvent, MailSignal

        interview = {
            "id": "fixture-gmail-1", "subject": "Example Labs technical interview invitation",
            "sender": "recruiter@examplelabs.com",
            "body": "Please schedule a technical interview for your application.",
            "received_at": timezone.now(),
            "link": "https://mail.google.com/mail/u/0/#all/abc123",
        }
        offer = {
            "id": "fixture-outlook-1", "subject": "Example Labs formal offer",
            "sender": "recruiter@examplelabs.com",
            "body": "We are pleased to offer you a position.",
            "received_at": timezone.now(),
            "link": "https://outlook.office.com/mail/id/abc123",
        }

        def fake_messages(connection, request):
            return [interview] if connection.provider == "google" else [offer]

        with patch("webapp.mailbox.load_messages", side_effect=fake_messages):
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
                try:
                    writer = self.open_board(browser.new_context(), self.user.username)
                    reader = self.open_board(browser.new_context(), self.user.username)

                    writer.get_by_role("button", name="Sync Gmail").click()
                    expect(writer.get_by_role("heading", name=interview["subject"])).to_be_visible()
                    expect(writer.get_by_role("button", name="Approve change")).to_be_visible()
                    self.application.refresh_from_db()
                    self.assertEqual(self.application.status, "applied")  # Consent still required.

                    # The other authenticated tab learns of the new proposal by polling.
                    expect(reader.get_by_role("heading", name=interview["subject"])).to_be_visible(timeout=20000)
                    reader.get_by_role("button", name="Approve change").click()
                    stage = reader.get_by_role("region", name="Interview", exact=True)
                    expect(stage.get_by_role("heading", name="Data Engineer")).to_be_visible()
                    expect(writer.get_by_role("region", name="Interview", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible(timeout=20000)
                    self.application.refresh_from_db()
                    self.assertEqual(self.application.status, "interview")
                    self.assertEqual(ApplicationStatusEvent.objects.filter(origin="google").count(), 1)

                    writer.get_by_role("button", name="Sync Gmail").click()
                    self.assertEqual(MailSignal.objects.filter(connection__provider="google").count(), 1)

                    writer.get_by_role("button", name="Sync Outlook").click()
                    expect(writer.get_by_role("heading", name=offer["subject"])).to_be_visible()
                    self.application.refresh_from_db()
                    self.assertEqual(self.application.status, "interview")
                    writer.get_by_role("button", name="Dismiss").click()
                    self.application.refresh_from_db()
                    self.assertEqual(self.application.status, "interview")

                    outsider = self.open_board(browser.new_context(), self.other_user.username)
                    self.assertEqual(outsider.get_by_role("heading", name=offer["subject"]).count(), 0)
                    self.assertEqual(outsider.get_by_role("heading", name=interview["subject"]).count(), 0)
                finally:
                    browser.close()
