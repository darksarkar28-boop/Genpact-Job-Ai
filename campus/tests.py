from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from webapp.models import Profile
from webapp.tests import archive, pdf

from .models import AuditEvent, Feedback, Invitation, Membership, Organization, Revision, Role
from .services import invite_token, seats_used


class PortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.admin = users.create_user("placement_admin")
        cls.mentor = users.create_user("assigned_mentor")
        cls.candidate = users.create_user("member_one")
        cls.peer = users.create_user("another_member")
        cls.stranger = users.create_user("other_school")
        call_command("provision_campus", slug="college", name="College", seats=2, admin=cls.admin.username, verbosity=0)
        cls.org = Organization.objects.get(slug="college")
        cls.a = cls.org.cohorts.create(name="Engineering")
        cls.b = cls.org.cohorts.create(name="Design")

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, *values):
        return reverse("campus:" + name, args=[self.org.slug, *values])

    def invite(self, emails, role=Role.CANDIDATE, cohort=None, **extra):
        return self.client.post(self.url("invite_members"), {
            "emails": emails, "role": role, "cohort": (cohort or self.a).pk, **extra,
        })

    def accept(self, client, user, email):
        inv = self.org.invitations.get(email=email, revoked_at__isnull=True, accepted_at__isnull=True)
        client.force_login(user)
        client.get(reverse("campus:invite", args=[invite_token(inv)]))
        page = client.post(reverse("campus:join"), {"invitation": str(inv.pk)})
        self.assertRedirects(page, self.url("dashboard"))
        return self.org.members.get(user=user)

    def candidate_client(self):
        self.invite("one@example.test")
        client = Client()
        member = self.accept(client, self.candidate, "one@example.test")
        return client, member

    def share(self, client, data=None, **overrides):
        fields = {"document": SimpleUploadedFile("resume.pdf", data or pdf(["Candidate resume"])),
                  "consent": "on", "current_review": 0, "current_revision": 0, "current_cohort": self.a.pk}
        return client.post(self.url("share"), {**fields, **overrides})

    def test_bulk_reservation_exhaustion_deactivation_and_reassignment(self):
        result = self.invite("ONE@example.test, one@example.test\ntwo@example.test")
        self.assertRedirects(result, self.url("dashboard"))
        self.assertEqual(seats_used(self.org), 2)
        self.assertRedirects(self.invite("one@example.test"), self.url("dashboard"))
        self.assertEqual(self.org.invitations.count(), 2)
        self.assertEqual(self.invite("third@example.test, fourth@example.test").status_code, 409)
        self.assertEqual(self.org.invitations.count(), 2)
        self.assertEqual(self.invite("one@example.test", Role.MENTOR).status_code, 409)
        self.assertRedirects(self.invite("mentor@example.test", Role.MENTOR), self.url("dashboard"))
        own = Client()
        candidate = self.accept(own, self.candidate, "one@example.test")
        self.assertEqual(seats_used(self.org), 2)
        self.assertRedirects(self.client.post(self.url("manage_member", candidate.pk), {"action": "deactivate"}), self.url("dashboard"))
        self.assertEqual(own.get(self.url("dashboard")).status_code, 404)
        self.assertRedirects(self.invite("third@example.test"), self.url("dashboard"))
        third = self.accept(Client(), self.peer, "third@example.test")
        self.assertEqual(third.cohort_id, self.a.pk)
        self.assertEqual(seats_used(self.org), 2)
        admin = self.org.members.get(user=self.admin)
        self.assertEqual(self.client.post(self.url("manage_member", admin.pk), {"action": "deactivate"}).status_code, 409)

    def test_cohort_denominators_reflect_real_profile_and_self_reported_outcomes(self):
        one, _ = self.candidate_client()
        self.invite("rew@example.test", cohort=self.b)
        self.accept(Client(), self.peer, "rew@example.test")
        Profile.objects.create(user=self.candidate, revision=1, data={"full_name": "Private profile detail"})
        self.assertEqual(one.post(self.url("progress"), {"applications": -1, "interviews": 1, "offers": 0}).status_code, 422)
        one.post(self.url("progress"), {"applications": 5, "interviews": 2, "offers": 1})
        all_cohorts = self.client.get(self.url("dashboard"))
        self.assertEqual(all_cohorts.context["stats"], {"total": 2, "onboarded": 1, "reporting": 1,
                                                       "applied": 1, "interviewed": 1, "offered": 1})
        self.assertNotContains(all_cohorts, "Private profile detail")
        scoped = self.client.get(self.url("dashboard"), {"cohort": self.b.pk})
        self.assertEqual(scoped.context["stats"]["total"], 1)
        self.assertEqual(scoped.context["stats"]["reporting"], 0)
        self.assertNotContains(scoped, self.candidate.username + "</strong>")
        self.invite("mentor@example.test", Role.MENTOR)
        coach = Client()
        mentor = self.accept(coach, self.mentor, "mentor@example.test")
        self.assertEqual(coach.get(self.url("dashboard")).context["stats"]["total"], 1)
        self.assertEqual(coach.get(self.url("dashboard"), {"cohort": self.b.pk}).status_code, 404)
        self.client.post(self.url("manage_member", mentor.pk), {"action": "assign", "cohorts": [self.a.pk, self.b.pk]})
        self.assertEqual(coach.get(self.url("dashboard")).context["stats"]["total"], 2)

    def test_revision_history_feedback_and_immediate_withdrawal(self):
        own, member = self.candidate_client()
        self.invite("mentor@example.test", Role.MENTOR)
        coach = Client()
        self.accept(coach, self.mentor, "mentor@example.test")
        self.assertEqual(self.share(own, consent="").status_code, 422)
        data = pdf(["Original revision", "Candidate selected content."])
        self.assertEqual(self.share(own, data).status_code, 302)
        review = member.reviews.get()
        first = review.revisions.get()
        page = self.url("review", review.pk)
        download = self.url("download", review.pk, first.pk)
        self.assertEqual(coach.get(download).content, data)
        self.assertIn("no-store", coach.get(download)["Cache-Control"])
        self.assertEqual(self.share(own, data).status_code, 302)
        self.assertEqual(review.revisions.count(), 1)
        self.assertEqual(coach.post(self.url("comment", review.pk, first.pk), {"comment": "Clarify your contribution."}).status_code, 302)
        self.assertContains(own.get(page), "Clarify your contribution.")
        word = archive({"[Content_Types].xml": "<Types/>", "word/document.xml": "<document/>"})
        fields = {"document": SimpleUploadedFile("updated.docx", word), "current_review": review.pk, "current_revision": 1}
        self.assertEqual(self.share(own, **fields).status_code, 302)
        self.assertEqual(list(review.revisions.values_list("number", flat=True)), [2, 1])
        self.assertEqual(self.share(own, pdf(["Outdated tab"]), current_review=review.pk, current_revision=1).status_code, 409)
        self.assertEqual(Feedback.objects.get().revision_id, first.pk)
        self.assertEqual(own.post(self.url("withdraw", review.pk)).status_code, 302)
        self.assertEqual(coach.get(page).status_code, 404)
        self.assertEqual(coach.get(download).status_code, 404)
        self.assertEqual(coach.post(self.url("comment", review.pk, first.pk), {"comment": "late"}).status_code, 404)
        self.assertContains(own.get(page), "Clarify your contribution.")
        self.assertTrue(AuditEvent.objects.filter(action="Withdrew résumé access").exists())
        self.assertFalse(AuditEvent.objects.filter(subject__contains="Clarify").exists())

    def test_cross_organization_and_cohort_access_admin_and_candidate_boundaries(self):
        own, member = self.candidate_client()
        self.share(own)
        review = member.reviews.get()
        revision = review.revisions.get()
        other_org = Organization.objects.create(name="Other school", slug="other", seat_limit=3)
        elsewhere = other_org.cohorts.create(name="Engineering")
        Membership.objects.create(organization=other_org, role=Role.ADMIN, user=self.stranger)
        outsider = Client()
        outsider.force_login(self.stranger)
        self.assertEqual(outsider.get(self.url("dashboard")).status_code, 404)
        self.assertEqual(outsider.get(reverse("campus:download", args=["other", review.pk, revision.pk])).status_code, 404)
        self.assertEqual(outsider.post(reverse("campus:manage_member", args=["other", member.pk]), {"action": "deactivate"}).status_code, 404)
        self.assertEqual(self.invite("forged@example.test", cohort=elsewhere).status_code, 422)
        self.assertEqual(self.client.post(self.url("manage_member", member.pk), {"action": "assign", "cohorts": [elsewhere.pk]}).status_code, 404)
        self.assertEqual(self.client.get(self.url("download", review.pk, revision.pk)).status_code, 404)
        self.invite("mentor@example.test", Role.MENTOR, cohort=self.b)
        coach = Client()
        self.accept(coach, self.mentor, "mentor@example.test")
        self.assertEqual(coach.get(self.url("review", review.pk)).status_code, 404)
        self.assertEqual(coach.post(self.url("invite_members"), {}).status_code, 404)
        self.invite("peer@example.test")
        peer = Client()
        self.accept(peer, self.peer, "peer@example.test")
        self.assertEqual(peer.get(self.url("review", review.pk)).status_code, 404)
        self.assertEqual(peer.get(self.url("download", review.pk, revision.pk)).status_code, 404)
        self.assertEqual(peer.post(self.url("withdraw", review.pk)).status_code, 404)
        self.assertEqual(own.post(self.url("manage_member", member.pk), {"action": "deactivate"}).status_code, 404)

    def test_cohort_transfer_and_deactivation_do_not_republish_old_material(self):
        own, member = self.candidate_client()
        self.share(own)
        old = member.reviews.get()
        self.client.post(self.url("manage_member", member.pk), {"action": "assign", "cohorts": [self.b.pk]})
        old.refresh_from_db()
        self.assertIsNotNone(old.withdrawn_at)
        self.assertEqual(self.share(own).status_code, 409)
        self.assertEqual(self.share(own, current_cohort=self.b.pk).status_code, 302)
        self.assertEqual(member.reviews.count(), 2)
        self.client.post(self.url("manage_member", member.pk), {"action": "deactivate"})
        self.assertFalse(member.reviews.filter(withdrawn_at__isnull=True).exists())

    @override_settings(CAMPUS_MAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_batch_and_fallback_on_mail_failure(self):
        self.invite("one@example.test,two@example.test", deliver="on")
        self.assertCountEqual([x.to for x in mail.outbox], [["one@example.test"], ["two@example.test"]])
        self.assertIn("/campus/invite/", mail.outbox[0].body)
        self.assertNotIn("two@example.test", mail.outbox[0].body)
        self.invite("one@example.test", deliver="on")
        self.assertEqual(len(mail.outbox), 2)
        with patch("campus.views.send_mail", side_effect=OSError("unavailable")):
            self.invite("mentor@example.test", Role.MENTOR, deliver="on")
        self.assertContains(self.client.get(self.url("dashboard")), "Email delivery failed")
        self.assertTrue(self.org.invitations.filter(email="mentor@example.test").exists())

    def test_expired_revoked_and_replayed_invitations_and_license_floor(self):
        self.invite("one@example.test,two@example.test")
        first, second = list(self.org.invitations.order_by("email"))
        with self.assertRaises(CommandError):
            call_command("provision_campus", slug="college", name="College", seats=1, admin=self.admin.username)
        Invitation.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.client.post(self.url("revoke", second.pk))
        anonymous = Client()
        for inv in (first, second):
            self.assertEqual(anonymous.get(reverse("campus:invite", args=[invite_token(inv)])).status_code, 410)
        self.assertEqual(seats_used(self.org), 0)
        self.invite("third@example.test")
        accepted = self.org.invitations.get(email="third@example.test")
        self.accept(Client(), self.candidate, "third@example.test")
        self.assertEqual(anonymous.get(reverse("campus:invite", args=[invite_token(accepted)])).status_code, 410)
        self.assertEqual(anonymous.get(reverse("campus:invite", args=[str(accepted.pk)])).status_code, 404)
        self.assertEqual(seats_used(self.org), 1)

    def test_private_methods_csrf_and_upload_limits(self):
        anon = Client()
        self.assertEqual(anon.get(self.url("dashboard")).status_code, 302)
        self.assertEqual(self.client.get(self.url("invite_members")).status_code, 405)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.admin)
        self.assertEqual(guarded.post(self.url("create_cohort"), {"name": "Unsafe"}).status_code, 403)
        own, _ = self.candidate_client()
        self.assertEqual(self.share(own, document=SimpleUploadedFile("invalid.docx", b"not a document")).status_code, 422)
        self.assertEqual(own.generic("POST", self.url("share"), CONTENT_LENGTH="5000000").status_code, 413)
        self.assertEqual(Revision.objects.count(), 0)
