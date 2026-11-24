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
                    expect(writer.get_by_role("region", name="Applied", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible()  # Consent still required.

                    # The other authenticated tab learns of the new proposal by polling.
                    expect(reader.get_by_role("heading", name=interview["subject"])).to_be_visible(timeout=20000)
                    reader.get_by_role("button", name="Approve change").click()
                    stage = reader.get_by_role("region", name="Interview", exact=True)
                    expect(stage.get_by_role("heading", name="Data Engineer")).to_be_visible()
                    expect(writer.get_by_role("region", name="Interview", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible(timeout=20000)
                    writer.get_by_role("button", name="Sync Gmail").click()
                    self.assertEqual(writer.get_by_role("heading", name=interview["subject"]).count(), 1)

                    writer.get_by_role("button", name="Sync Outlook").click()
                    expect(writer.get_by_role("heading", name=offer["subject"])).to_be_visible()
                    expect(writer.get_by_role("region", name="Interview", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible()
                    writer.get_by_role("button", name="Dismiss").click()
                    expect(writer.get_by_role("region", name="Interview", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible()

                    outsider = self.open_board(browser.new_context(), self.other_user.username)
                    self.assertEqual(outsider.get_by_role("heading", name=offer["subject"]).count(), 0)
                    self.assertEqual(outsider.get_by_role("heading", name=interview["subject"]).count(), 0)
                finally:
                    browser.close()

        # Django ORM checks run after Playwright has left its async context.
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "interview")
        self.assertEqual(ApplicationStatusEvent.objects.filter(origin="google").count(), 1)
        self.assertEqual(MailSignal.objects.filter(connection__provider="google").count(), 1)
        self.assertEqual(MailSignal.objects.get(provider_message_id="fixture-outlook-1").state, "dismissed")


    def test_ambiguous_and_conflicting_mail_can_be_resolved_safely_in_two_sessions(self):
        """The candidate selects the right employer and stage, without overriding another tab."""
        from unittest.mock import patch

        from django.utils import timezone

        from .mail_classifier import import_signals
        from .models import Application, ApplicationStatusEvent, MailConnection, MailSignal

        second = Application.objects.create(
            user=self.user, company="Example Labs Research",
            role="Research Engineer", status="applied",
        )
        conflict_app = Application.objects.create(
            user=self.user, company="Unique Vision Labs",
            role="Analyst", status="applied",
        )
        now = timezone.now()
        ambiguous = {
            "id": "ambiguous-browser", "subject": "Example Labs and Example Labs Research interview invitation",
            "sender": "hr@examplelabs.com", "body": "Please schedule an interview for this application.",
            "received_at": now,
            "link": "https://mail.google.com/mail/u/0/#all/ambiguousbrowser",
        }
        conflict1 = {
            "id": "conflict-browser-1", "subject": "Unique Vision Labs technical interview invitation",
            "sender": "hr@uniquevisionlabs.com", "body": "Please schedule a technical interview.",
            "received_at": now,
            "link": "https://mail.google.com/mail/u/0/#all/conflictbrowser1",
        }
        conflict2 = {
            "id": "conflict-browser-2", "subject": "Unique Vision Labs formal offer letter",
            "sender": "hr@uniquevisionlabs.com", "body": "We are pleased to offer a position.",
            "received_at": now,
            "link": "https://mail.google.com/mail/u/0/#all/conflictbrowser2",
        }
        originals = [ambiguous, conflict1, conflict2]
        connection = MailConnection.objects.get(user=self.user, provider="google")
        self.assertEqual(import_signals(connection, originals), 3)
        self.assertEqual(
            set(MailSignal.objects.values_list("state", flat=True)),
            {"needs_review"},
        )

        with patch("webapp.mailbox.load_messages", return_value=originals):
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
                try:
                    writer = self.open_board(browser.new_context(), self.user.username)
                    reader = self.open_board(browser.new_context(), self.user.username)
                    article = writer.locator("article.mail-signal").filter(has_text=ambiguous["subject"])
                    expect(article).to_be_visible()
                    expect(writer.locator("article.mail-signal").filter(
                        has_text=conflict2["subject"],
                    )).to_be_visible()

                    article.get_by_label("Application for this email").select_option(f"{self.application.pk}:0")
                    article.get_by_label("Intended stage").select_option("interview")
                    article.get_by_role("checkbox", name=re.compile("I reviewed the source email")).check()

                    # A different signed-in tab makes a manual change after the
                    # candidate first chose an application for the email.
                    other_card = reader.locator("article.board-card").filter(
                        has=reader.get_by_role("heading", name="Data Engineer"),
                    )
                    other_card.get_by_label("Move to stage").select_option("rejected")
                    other_card.get_by_role("button", name="Save change").click()
                    expect(reader.get_by_role("region", name="Rejected", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible()

                    article.get_by_role("button", name="Resolve manually").click()
                    expect(writer.locator("body")).to_contain_text(
                        "Application stage changed in another session",
                    )

                    # New explicit decision targets the other plausible employer,
                    # and the reader tab observes a single source-linked stage.
                    writer.goto(self.live_server_url + reverse("application_board"))
                    article = writer.locator("article.mail-signal").filter(has_text=ambiguous["subject"])
                    article.get_by_label("Application for this email").select_option(f"{second.pk}:0")
                    article.get_by_label("Intended stage").select_option("interview")
                    article.get_by_role("checkbox", name=re.compile("I reviewed the source email")).check()
                    article.get_by_role("button", name="Resolve manually").click()

                    reader.bring_to_front()
                    review_card = reader.locator("article.board-card").filter(
                        has=reader.get_by_role("heading", name="Research Engineer"),
                    )
                    interview = reader.get_by_role("region", name="Interview", exact=True)
                    expect(interview.get_by_role("heading", name="Research Engineer")).to_be_visible(timeout=22000)
                    review_card.get_by_text("History", exact=True).click()
                    expect(review_card.get_by_role("link", name="Source email")).to_be_visible()
                    expect(reader.get_by_role("region", name="Rejected", exact=True).get_by_role(
                        "heading", name="Data Engineer",
                    )).to_be_visible()

                    writer.get_by_role("button", name="Sync Gmail").click()
                    writer.get_by_role("button", name="Sync Gmail").click()
                    expect(writer.get_by_role("region", name="Interview", exact=True).get_by_role(
                        "heading", name="Research Engineer",
                    )).to_be_visible()
                finally:
                    browser.close()

        self.application.refresh_from_db()
        second.refresh_from_db()
        conflict_app.refresh_from_db()
        self.assertEqual((self.application.status, second.status, conflict_app.status),
                         ("rejected", "interview", "applied"))
        self.assertEqual(MailSignal.objects.count(), 3)  # No replay duplicates
        self.assertEqual(ApplicationStatusEvent.objects.filter(
            application=second, origin="manual_mail_review",
        ).count(), 1)
        self.assertEqual(ApplicationStatusEvent.objects.get(application=second).source_message_id,
                         ambiguous["id"])
