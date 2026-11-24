from datetime import datetime, timedelta, timezone as utc
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tools.job_feeds import ingest
from tools.partner_job_feeds import ingest_partner
from webapp.models import Profile
from .mail import deliver_due
from .models import Delivery, Posting, Preference, Source
from .services import refresh, ranked, persist

CONFIG = '{"boards":[{"provider":"greenhouse","slug":"acme","employer":"Acme","region":"india"}],"partners":[{"provider":"naukri","endpoint":"https://relay.example/jobs","token":"fake"}]}'
ROW = {"id": 10, "title": "Python engineer", "location": {"name": "Bengaluru"},
       "absolute_url": "https://example.org/10?utm_source=x", "content": "<p>Python Django</p>"}


@override_settings(JOBFEEDS_CONFIG=CONFIG, JOBFEEDS_MAIL_ENABLED=True,
                   JOBFEEDS_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   JOBFEEDS_FROM_EMAIL="jobs@example.org")
class JobFeedAcceptance(TestCase):
    def setUp(self):
        self.a = get_user_model().objects.create_user(username="candidate-a")
        self.b = get_user_model().objects.create_user(username="candidate-b")
        Profile.objects.create(user=self.a, revision=1, data={"skills": ["Python", "Django", "Rust"]})
        Profile.objects.create(user=self.b, revision=1, data={"skills": ["Rust"]})

    def public(self, board, rows=None):
        return ingest(board, fetch=lambda _: {"jobs": [ROW] if rows is None else rows})

    def partner(self, feed):
        return ingest_partner(feed, fetch=lambda *_: {"version": 1, "provider": "naukri", "jobs": [
            {"source_id": "duplicate", "employer": "Acme", "title": "Python engineer", "location": "Bengaluru",
             "application_url": "https://example.org/10", "description": "Python Django"},
            {"source_id": "other", "employer": "Other", "title": "Rust engineer", "location": "Pune",
             "application_url": "https://example.org/11", "description": "Rust"},
        ]})

    def populate(self):
        return refresh(public_reader=self.public, partner_reader=self.partner)

    def test_refresh_dedup_market_role_location_profile_and_replay(self):
        self.assertEqual(self.populate(), [("greenhouse:acme", "ok"), ("naukri:licensed", "ok")])
        self.populate()
        self.assertEqual(Posting.objects.count(), 3)
        a = Preference.objects.create(user=self.a, market="india", role="Python", location="Bengaluru")
        b = Preference.objects.create(user=self.b, market="india")
        self.assertEqual(len(ranked(a)), 1)
        self.assertEqual(ranked(a)[0]["score"], 67)
        self.assertEqual(ranked(b)[0]["job"].title, "Rust engineer")
        self.assertEqual(ranked(b)[0]["score"], 100)

    def test_partial_retains_complete_removes_and_stale_hides(self):
        self.populate()
        refresh(public_reader=lambda b: self.public(b, [{}]), partner_reader=self.partner)
        self.assertTrue(Posting.objects.get(source__provider="greenhouse").active)
        refresh(public_reader=lambda b: self.public(b, []), partner_reader=self.partner)
        self.assertFalse(Posting.objects.get(source__provider="greenhouse").active)
        Posting.objects.update(last_seen=timezone.now() - timedelta(days=8))
        pref = Preference.objects.create(user=self.a)
        self.assertEqual(ranked(pref), [])

    def test_expired_and_old_snapshot(self):
        self.populate()
        Posting.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        pref = Preference.objects.create(user=self.a)
        self.assertEqual(ranked(pref), [])
        source = Source.objects.get(provider="greenhouse")
        from tools.job_feeds import Board
        result = self.public(Board("greenhouse", "acme", "Acme"))
        self.assertFalse(persist(source, source.last_attempt - timedelta(seconds=1), result))

    def test_account_settings_csrf_and_all_provider_statuses(self):
        self.populate()
        url = reverse("jobfeeds:home")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.a)
        self.assertContains(self.client.get(url), "Internshala")
        values = {"market": "india", "role": "Python", "location": "Bengaluru", "zone": "Asia/Kolkata",
                  "hour": 8, "email": "a@example.org", "opted_in": "on"}
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.assertFalse(Preference.objects.get(user=self.a).opted_in)
        self.assertContains(self.client.get(url), "Keyword coverage: 67%")
        self.client.force_login(self.b)
        self.assertNotContains(self.client.get(url), "a@example.org")
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.a)
        self.assertEqual(strict.post(url, values).status_code, 403)
        self.assertEqual(self.client.post(url, dict(values, zone="invalid/zone")).status_code, 422)

    def test_email_verification_owner_replay_optin_and_delivery(self):
        self.populate()
        self.client.force_login(self.a)
        values = {"market": "india", "role": "", "location": "", "zone": "Asia/Kolkata",
                  "hour": 8, "email": "a@example.org", "opted_in": "on"}
        url = reverse("jobfeeds:home")
        self.client.post(url, values)
        self.assertEqual(self.client.post(reverse("jobfeeds:verify_send")).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        verification_url = mail.outbox[0].body.splitlines()[1].split("testserver", 1)[1]
        self.client.force_login(self.b)
        self.assertEqual(self.client.post(verification_url).status_code, 400)
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(verification_url).status_code, 200)
        self.assertFalse(Preference.objects.get(user=self.a).verified)
        self.assertEqual(self.client.post(verification_url).status_code, 302)
        self.assertEqual(self.client.post(verification_url).status_code, 400)
        self.client.post(url, values)
        self.assertTrue(Preference.objects.get(user=self.a).opted_in)
        # Use a current India-local morning so the fixture jobs are fresh.
        now = timezone.now()
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("Asia/Kolkata")).replace(hour=8, minute=0, second=0, microsecond=0)
        due = local.astimezone(utc.utc)
        self.assertEqual(deliver_due(due - timedelta(hours=1)), [])
        self.assertEqual(deliver_due(due)[0][1], "sent")
        self.assertEqual(deliver_due(due), [])
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("https://example.org/10", mail.outbox[-1].body)
        self.assertEqual(mail.outbox[-1].to, ["a@example.org"])
        self.client.post(url, dict(values, opted_in=""))
        self.assertEqual(deliver_due(due + timedelta(days=1)), [])
        self.assertEqual(Delivery.objects.count(), 1)

    def test_ambiguous_delivery_failure_no_retry_and_email_change(self):
        pref = Preference.objects.create(user=self.a, verified=True, opted_in=True,
                                         email="a@example.org", zone="UTC", hour=8)
        now = datetime(2026, 11, 24, 8, tzinfo=utc.utc)
        sender = lambda *args: (_ for _ in ()).throw(OSError("ambiguous"))
        self.assertEqual(deliver_due(now, sender=sender)[0][1], "failed")
        self.assertEqual(deliver_due(now, sender=sender), [])
        self.client.force_login(self.a)
        self.client.post(reverse("jobfeeds:home"), {"market": "all", "role": "", "location": "",
            "zone": "UTC", "hour": 8, "email": "new@example.org", "opted_in": "on"})
        pref.refresh_from_db()
        self.assertFalse(pref.verified)
        self.assertFalse(pref.opted_in)

    def test_management_commands_and_unconfigured_status(self):
        with patch("jobfeeds.management.commands.refresh_job_feeds.refresh", return_value=[("greenhouse:acme", "ok")]) as refresh_mock:
            call_command("refresh_job_feeds")
            refresh_mock.assert_called_once()
        with override_settings(JOBFEEDS_MAIL_ENABLED=False):
            call_command("send_job_digests")
        with override_settings(JOBFEEDS_CONFIG='{"boards":[],"partners":[]}'):
            self.populate()
            self.assertFalse(Source.objects.filter(enabled=True).exists())
