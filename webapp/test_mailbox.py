"""Mailbox integration tests use provider doubles: never contact real inboxes."""
import os
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .mail_oauth import MailError, access_token, decrypt, encrypt
from .mail_providers import gmail_messages, outlook_messages
from .models import Application, ApplicationStatusEvent, MailConnection, MailSignal


class MailboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("mail_owner", password="mail-owner-pass-123")
        cls.other = get_user_model().objects.create_user("mail_other", password="mail-other-pass-123")

    def setUp(self):
        self.client.force_login(self.owner)
        self.env = patch.dict(os.environ, {
            "JOBWEB_GOOGLE_CLIENT_ID": "test-google", "JOBWEB_GOOGLE_CLIENT_SECRET": "test-secret",
            "JOBWEB_MICROSOFT_CLIENT_ID": "test-microsoft", "JOBWEB_MICROSOFT_CLIENT_SECRET": "test-secret",
            "JOBWEB_MAIL_TOKEN_KEY": Fernet.generate_key().decode(),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = Application.objects.create(
            user=self.owner, company="Example Labs", role="Data Engineer", status="drafted",
        )
        self.google = MailConnection.objects.create(
            user=self.owner, provider="google",
            access_token_ciphertext=encrypt("sensitive-example-token"),
            refresh_token_ciphertext=encrypt("refresh-example-token"),
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def mail(self, msg_id="001", *, subject="Example Labs received your application",
             body="We have received your application."):
        return {
            "id": msg_id, "subject": subject, "sender": "careers@examplelabs.com",
            "body": body, "received_at": timezone.now(),
            "link": "https://mail.google.com/mail/u/0/#all/abc123",
        }

    def sync(self, data):
        with patch("webapp.mailbox.load_messages", return_value=data):
            return self.client.post(reverse("mailbox_sync", args=["google"]))

    def test_oauth_state_pkce_encryption_and_disconnect(self):
        self.google.delete()
        start = self.client.post(reverse("mailbox_connect", args=["google"]))
        url = start["Location"]
        self.assertEqual(urlsplit(url).hostname, "accounts.google.com")
        qs = parse_qs(urlsplit(url).query)
        self.assertEqual(qs["scope"], ["https://www.googleapis.com/auth/gmail.readonly"])
        self.assertEqual(qs["code_challenge_method"], ["S256"])
        self.assertIn("state", qs)
        self.assertNotIn("access_token", url)
        callback = reverse("mailbox_callback", args=["google"])
        with patch("webapp.mail_oauth.oauth_request") as exchange:
            exchange.return_value = {
                "access_token": "new-secret-access", "refresh_token": "new-secret-refresh",
                "expires_in": 3500,
            }
            bad = self.client.get(callback, {"code": "code", "state": "wrong"})
            self.assertRedirects(bad, reverse("application_board"))
            exchange.assert_not_called()
            # The state is single-use; a mismatched callback cannot be replayed.
            again = self.client.get(callback, {"code": "code", "state": qs["state"][0]})
            self.assertRedirects(again, reverse("application_board"))
            exchange.assert_not_called()
        start = self.client.post(reverse("mailbox_connect", args=["google"]))
        state = parse_qs(urlsplit(start["Location"]).query)["state"][0]
        with patch("webapp.mail_oauth.oauth_request", return_value={
            "access_token": "new-secret-access", "refresh_token": "new-secret-refresh",
            "expires_in": 3500,
        }) as exchange:
            response = self.client.get(callback, {"code": "auth-code", "state": state})
            self.assertRedirects(response, reverse("application_board"))
            exchange.assert_called_once()
        connection = MailConnection.objects.get(user=self.owner, provider="google")
        self.assertNotIn("new-secret-access", connection.access_token_ciphertext)
        self.assertEqual(decrypt(connection.refresh_token_ciphertext), "new-secret-refresh")
        self.assertEqual(access_token(connection, None), "new-secret-access")
        self.assertRedirects(
            self.client.post(reverse("mailbox_disconnect", args=["google"])),
            reverse("application_board"),
        )
        self.assertFalse(MailConnection.objects.filter(user=self.owner).exists())

    def test_refresh_and_reconnect_flag_for_expired_credentials(self):
        self.google.expires_at = timezone.now() - timedelta(hours=1)
        self.google.save(update_fields=["expires_at"])
        with patch("webapp.mail_oauth.oauth_request", return_value={
            "access_token": "rotated", "refresh_token": "new-refresh", "expires_in": 3600,
        }) as refresh:
            self.assertEqual(access_token(self.google, NoneRequest()), "rotated")
            self.assertEqual(decrypt(self.google.refresh_token_ciphertext), "new-refresh")
            refresh.assert_called_once()
        self.google.refresh_token_ciphertext = ""
        self.google.expires_at = timezone.now() - timedelta(hours=1)
        self.google.save(update_fields=["refresh_token_ciphertext", "expires_at"])
        with self.assertRaises(MailError):
            access_token(self.google, NoneRequest())
        self.google.refresh_from_db()
        self.assertTrue(self.google.needs_reconnect)

    def test_mailbox_sync_requires_review_and_never_moves_status_automatically(self):
        self.assertRedirects(self.sync([self.mail()]), reverse("application_board"))
        signal = MailSignal.objects.get()
        self.assertEqual((signal.state, signal.proposed_status), ("pending", "applied"))
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "drafted")
        self.assertEqual(ApplicationStatusEvent.objects.count(), 0)
        self.assertContains(self.client.get(reverse("application_board")), "Approve change")
        decision = reverse("mailbox_decide", args=[signal.pk])
        self.assertRedirects(self.client.post(decision, {"action": "approve"}), reverse("application_board"))
        self.app.refresh_from_db()
        self.assertEqual((self.app.status, self.app.version), ("applied", 1))
        self.assertEqual(
            list(ApplicationStatusEvent.objects.values_list("before", "after", "origin")),
            [("drafted", "applied", "google")],
        )
        self.assertEqual(self.client.post(decision, {"action": "approve"}).status_code, 409)
        self.assertEqual(ApplicationStatusEvent.objects.count(), 1)
        self.assertEqual(ApplicationStatusEvent.objects.get().mail_signal_id, signal.pk)
        self.assertContains(self.client.get(reverse("application_board")), "Source email")

    def test_replayed_and_out_of_order_mail_cannot_double_apply(self):
        first = self.mail()
        self.sync([first, first])
        self.sync([first])
        self.assertEqual(MailSignal.objects.count(), 1)
        signal = MailSignal.objects.get()
        self.client.post(reverse("application_move", args=[self.app.pk]), {
            "status": "interview", "version": 0,
        })
        self.assertEqual(
            self.client.post(reverse("mailbox_decide", args=[signal.pk]), {"action": "approve"}).status_code,
            409,
        )
        signal.refresh_from_db()
        self.assertEqual(signal.state, "stale")
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "interview")
        self.assertEqual(ApplicationStatusEvent.objects.count(), 1)

    def test_conflicting_signals_and_no_ambiguous_employer_match(self):
        self.sync([
            self.mail("off", subject="Example Labs job offer", body="Please review your formal offer."),
            self.mail("rej", subject="Example Labs application result", body="We are moving forward with other candidates."),
        ])
        self.assertEqual(set(MailSignal.objects.values_list("state", flat=True)), {"needs_review"})
        self.assertFalse(self.client.get(reverse("application_board")).content.find(b"Conflicting") == -1)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "drafted")
        signal = MailSignal.objects.first()
        self.assertEqual(
            self.client.post(reverse("mailbox_decide", args=[signal.pk]), {"action": "approve"}).status_code, 409)
        self.sync([self.mail("other", subject="Hello", body="Generic message")])
        self.assertEqual(MailSignal.objects.get(provider_message_id="other").state, "ignored")
        self.assertEqual(MailSignal.objects.get(provider_message_id="other").subject, "")
        ambiguous = self.mail("ambiguous", subject="Technical interview invitation", body="Schedule a technical interview")
        ambiguous["sender"] = "noreply@unknown.test"
        self.sync([ambiguous])
        self.assertEqual(MailSignal.objects.get(provider_message_id="ambiguous").state, "needs_review")

    def test_mail_signals_and_connections_are_isolated_by_account(self):
        self.sync([self.mail()])
        signal = MailSignal.objects.get()
        other_client = Client()
        other_client.force_login(self.other)
        self.assertNotContains(other_client.get(reverse("application_board")), signal.subject)
        self.assertEqual(other_client.post(reverse("mailbox_sync", args=["google"])).status_code, 404)
        self.assertEqual(
            other_client.post(reverse("mailbox_decide", args=[signal.pk]), {"action": "approve"}).status_code,
            404,
        )
        self.assertEqual(other_client.post(reverse("mailbox_disconnect", args=["google"])).status_code, 302)
        self.assertTrue(MailSignal.objects.filter(pk=signal.pk).exists())
        self.assertEqual(Client().post(reverse("mailbox_sync", args=["google"])).status_code, 302)

    def test_gmail_and_outlook_adapter_fixtures(self):
        import base64
        encoded = base64.urlsafe_b64encode(b"Schedule a technical interview.").decode().rstrip("=")
        with patch("webapp.mail_providers.provider_get", side_effect=[
            {"messages": [{"id": "gmail-msg-1"}]},
            {
                "id": "gmail-msg-1", "threadId": "abc123", "internalDate": "1790000000000",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Example Labs interview"},
                                {"name": "From", "value": "Recruiter <jobs@examplelabs.com>"}],
                    "parts": [{"mimeType": "text/plain", "body": {"data": encoded}}],
                },
            },
        ]) as get:
            gmail = gmail_messages("fake-token")
            self.assertEqual(len(gmail), 1)
            self.assertEqual(gmail[0]["body"], "Schedule a technical interview.")
            self.assertTrue(gmail[0]["link"].startswith("https://mail.google.com/"))
            self.assertEqual(get.call_count, 2)
        with patch("webapp.mail_providers.provider_get", return_value={
            "value": [{
                "id": "outlook-msg-1", "subject": "Example Labs interview invitation",
                "from": {"emailAddress": {"name": "Recruiter", "address": "jobs@examplelabs.com"}},
                "bodyPreview": "Please schedule an interview",
                "receivedDateTime": "2026-11-23T12:00:00Z",
                "webLink": "https://outlook.office.com/mail/id/abc",
            }],
        }):
            outlook = outlook_messages("fake-token")
            self.assertEqual(outlook[0]["id"], "outlook-msg-1")
            self.assertEqual(outlook[0]["sender"], "Recruiter jobs@examplelabs.com")

    def test_invalid_method_and_csrf_guards(self):
        self.assertEqual(self.client.get(reverse("mailbox_connect", args=["google"])).status_code, 405)
        self.assertEqual(self.client.get(reverse("mailbox_sync", args=["google"])).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(reverse("mailbox_disconnect", args=["google"])).status_code, 403)


class NoneRequest:
    """Only the provider configuration request object is used for token refresh."""
    def build_absolute_uri(self, value):
        return "http://testserver" + value

    def get_host(self):
        return "testserver"



class AdditionalMailboxSafetyTests(TestCase):
    def setUp(self):
        from .mail_oauth import encrypt
        self.user = get_user_model().objects.create_user("safe-mail-user", password="safe-mail-test-2026")
        self.env = patch.dict(os.environ, {"JOBWEB_MAIL_TOKEN_KEY": Fernet.generate_key().decode()})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.application = Application.objects.create(
            user=self.user, company="Example Labs", role="Data Engineer", status="applied",
        )
        self.connection = MailConnection.objects.create(
            user=self.user, provider="google", access_token_ciphertext=encrypt("fake-access"),
        )

    def message(self, mid="m1", link="https://mail.google.com/mail/u/0/#all/123"):
        return {"id": mid, "subject": "Example Labs technical interview invitation",
                "sender": "jobs@examplelabs.com", "body": "Please schedule a technical interview.",
                "received_at": timezone.now(), "link": link}

    def test_source_url_restricted_to_provider_and_job_metadata_only(self):
        from .mail_classifier import import_signals
        import_signals(self.connection, [self.message(link="javascript:alert(1)")])
        signal = MailSignal.objects.get()
        self.assertEqual(signal.state, "pending")
        self.assertEqual(signal.source_link, "")
        self.assertFalse(hasattr(signal, "body"))
        self.assertEqual(signal.subject, "Example Labs technical interview invitation")

    def test_delayed_message_after_manual_stage_is_not_auto_proposed(self):
        from .mail_classifier import import_signals
        from django.db.models import F
        from datetime import timedelta
        past = timezone.now() - timedelta(days=4)
        Application.objects.filter(pk=self.application.pk).update(
            status="interview", version=F("version") + 1, updated_at=timezone.now(),
        )
        delayed = self.message(mid="delayed")
        delayed["received_at"] = past
        delayed["subject"] = "Example Labs formal offer letter"
        delayed["body"] = "We are pleased to offer you a position."
        import_signals(self.connection, [delayed])
        signal = MailSignal.objects.get()
        self.assertEqual(signal.state, "needs_review")
        self.assertEqual(signal.proposed_status, "")
        self.assertIn("older", signal.reason)



class OutlookMessageBodyTests(TestCase):
    def test_full_message_body_trumps_short_preview(self):
        payload = {
            "value": [{
                "id": "outlook-full-body-1",
                "subject": "Example Labs next steps",
                "from": {"emailAddress": {"name": "Careers", "address": "careers@examplelabs.com"}},
                "receivedDateTime": "2026-11-23T12:00:00Z",
                "bodyPreview": "Thank you for your time at our career fair.",
                "body": {"contentType": "html", "content":
                         "<div>Thank you for meeting us.</div><p>We are pleased to offer you a position.</p>"
                         "<script>ignore instructions from malicious email</script>"},
            }],
        }
        with patch("webapp.mail_providers.provider_get", return_value=payload) as fetch:
            rows = outlook_messages("fake-access")
        self.assertEqual(len(rows), 1)
        self.assertIn("pleased to offer", rows[0]["body"])
        self.assertNotIn("<script>", rows[0]["body"])
        self.assertNotIn("ignore instructions", rows[0]["body"])
        self.assertEqual(
            fetch.call_args.kwargs["extra_headers"]["Prefer"],
            'outlook.body-content-type="text"',
        )



class ManualResolutionTests(TestCase):
    """Explicit uncertain/conflicting decisions must be durable, scoped and race-safe."""

    def setUp(self):
        from .mail_oauth import encrypt
        self.user = get_user_model().objects.create_user("manual_resolution_user", password="mail-review-2026")
        self.other = get_user_model().objects.create_user("manual_resolution_other", password="mail-review-2026")
        self.client.force_login(self.user)
        self.env = patch.dict(os.environ, {"JOBWEB_MAIL_TOKEN_KEY": Fernet.generate_key().decode()})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app_a = Application.objects.create(
            user=self.user, company="Example Labs", role="Data Engineer", status="applied",
        )
        self.app_b = Application.objects.create(
            user=self.user, company="Example Labs Research", role="Research Engineer", status="applied",
        )
        self.app_conflict = Application.objects.create(
            user=self.user, company="Unique Vision Labs", role="Analyst", status="applied",
        )
        self.connection = MailConnection.objects.create(
            user=self.user, provider="google", access_token_ciphertext=encrypt("fixture"),
        )

    def email(self, mid, subject):
        return {
            "id": mid, "subject": subject,
            "sender": "recruiter@examplelabs.com",
            "body": "Please review your job application and interview invitation.",
            "link": "https://mail.google.com/mail/u/0/#all/" + mid,
            "received_at": timezone.now(),
        }

    def import_messages(self, rows):
        from .mail_classifier import import_signals
        return import_signals(self.connection, rows)

    def resolve(self, signal, app, stage="interview", *, version=None, client=None, **extra):
        return (client or self.client).post(
            reverse("mailbox_decide", args=[signal.pk]),
            {"action": "resolve", "application_choice": f"{app.pk}:{app.version if version is None else version}",
             "status": stage, "confirm_resolution": "yes", **extra},
        )

    def test_ambiguous_match_manual_version_race_and_replay(self):
        candidate = self.email("ambiguous-1", "Example Labs and Example Labs Research interview invitation")
        self.assertEqual(self.import_messages([candidate]), 1)
        signal = MailSignal.objects.get(provider_message_id="ambiguous-1")
        self.assertEqual(signal.state, "needs_review")
        self.assertIsNone(signal.application_id)

        other_tab = Client()
        other_tab.force_login(self.user)
        before = other_tab.get(reverse("application_board_revision")).json()["revision"]
        # Someone moves app A while the review form still holds app A version zero.
        self.assertRedirects(
            other_tab.post(reverse("application_move", args=[self.app_a.pk]), {
                "version": 0, "status": "rejected",
            }), reverse("application_board"),
        )
        self.assertEqual(self.resolve(signal, self.app_a, version=0).status_code, 409)
        signal.refresh_from_db()
        self.assertEqual(signal.state, "needs_review")
        self.assertEqual(self.app_a.events.count(), 1)
        self.assertRedirects(self.resolve(signal, self.app_b, version=0), reverse("application_board"))
        self.app_a.refresh_from_db()
        self.app_b.refresh_from_db()
        signal.refresh_from_db()
        self.assertEqual((self.app_a.status, self.app_b.status), ("rejected", "interview"))
        self.assertEqual(signal.application_id, self.app_b.pk)
        event = ApplicationStatusEvent.objects.get(application=self.app_b)
        self.assertEqual((event.origin, event.mail_signal_id), ("manual_mail_review", signal.pk))
        self.assertEqual(event.source_message_id, "ambiguous-1")
        self.assertEqual(event.source_message_link, candidate["link"])
        self.assertNotEqual(before, other_tab.get(reverse("application_board_revision")).json()["revision"])

        self.assertEqual(self.import_messages([candidate]), 0)
        self.assertEqual(MailSignal.objects.filter(provider_message_id="ambiguous-1").count(), 1)
        self.assertEqual(self.resolve(signal, self.app_b, version=0).status_code, 409)
        self.assertEqual(self.app_b.events.count(), 1)
        self.assertContains(self.client.get(reverse("application_board")), "Source email")

    def test_conflicting_proposals_require_resolution_and_only_one_transition(self):
        interview = self.email("conflict-1", "Unique Vision Labs technical interview invitation")
        offer = self.email("conflict-2", "Unique Vision Labs formal offer letter")
        self.assertEqual(self.import_messages([interview, offer]), 2)
        first = MailSignal.objects.get(provider_message_id="conflict-1")
        second = MailSignal.objects.get(provider_message_id="conflict-2")
        self.assertEqual((first.state, second.state), ("needs_review", "needs_review"))
        self.assertEqual(self.app_conflict.status, "applied")
        self.assertRedirects(self.resolve(first, self.app_conflict), reverse("application_board"))
        self.app_conflict.refresh_from_db()
        self.assertEqual((self.app_conflict.status, self.app_conflict.version), ("interview", 1))
        self.assertEqual(self.app_conflict.events.count(), 1)
        self.assertEqual(self.resolve(second, self.app_conflict, version=0, stage="offer").status_code, 409)
        self.assertRedirects(
            self.resolve(second, self.app_conflict, version=1, stage="offer"), reverse("application_board"),
        )
        self.app_conflict.refresh_from_db()
        self.assertEqual((self.app_conflict.status, self.app_conflict.events.count()), ("offer", 2))
        self.assertEqual(set(self.app_conflict.events.values_list("source_message_id", flat=True)),
                         {"conflict-1", "conflict-2"})
        self.assertEqual(self.import_messages([interview, offer]), 0)
        self.assertEqual(self.app_conflict.events.count(), 2)

    def test_offer_decision_dismissal_and_disconnection_keep_audit_ref(self):
        email = self.email("manual-offer", "Example Labs and Example Labs Research offer letter")
        self.import_messages([email])
        signal = MailSignal.objects.get(provider_message_id="manual-offer")
        self.assertEqual(self.resolve(signal, self.app_b, stage="hired", version=0).status_code, 422)
        self.assertEqual(
            self.resolve(signal, self.app_b, stage="hired", version=0,
                         confirm_offer_decision="yes").status_code, 302,
        )
        self.app_b.refresh_from_db()
        self.assertEqual(self.app_b.status, "hired")
        self.client.post(reverse("mailbox_disconnect", args=["google"]))
        self.assertFalse(MailSignal.objects.exists())
        event = ApplicationStatusEvent.objects.get(application=self.app_b)
        self.assertIsNone(event.mail_signal_id)
        self.assertEqual((event.source_message_id, event.source_message_link),
                         ("manual-offer", email["link"]))
        self.assertContains(self.client.get(reverse("application_board")), "Source email")

    def test_foreign_application_and_csrf_cannot_resolve(self):
        email = self.email("cross-account", "Example Labs and Example Labs Research interview invitation")
        self.import_messages([email])
        signal = MailSignal.objects.get()
        foreign_app = Application.objects.create(user=self.other, company="Other", role="Analyst")
        self.assertEqual(self.resolve(signal, foreign_app).status_code, 404)
        other_session = Client()
        other_session.force_login(self.other)
        self.assertEqual(self.resolve(signal, self.app_b, client=other_session).status_code, 404)
        self.assertEqual(signal.state, "needs_review")
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(self.resolve(signal, self.app_b, client=csrf).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("mailbox_decide", args=[signal.pk]), {
                "action": "resolve", "application_choice": f"{self.app_b.pk}:0",
                "status": "interview",
            }).status_code, 422,
        )
