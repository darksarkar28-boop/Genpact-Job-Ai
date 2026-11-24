"""Issue #5 first vertical slice: ownership, history, concurrency and cross-session refresh."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Application, ApplicationStatus, ApplicationStatusEvent


class ApplicationBoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("board_owner", password="board-pwd-very-long")
        cls.other = get_user_model().objects.create_user("board_other", password="board-pwd-very-long")

    def setUp(self):
        self.client.force_login(self.owner)

    def create(self, **extra):
        return self.client.post(reverse("application_create"), {
            "company": "Example Labs", "role": "Data Engineer", "status": "drafted", **extra,
        })

    def move(self, app, status, version=None, client=None):
        return (client or self.client).post(reverse("application_move", args=[app.pk]), {
            "status": status, "version": app.version if version is None else version,
        })

    def test_create_move_and_history_persist_on_reload(self):
        self.assertRedirects(self.create(), reverse("application_board"))
        app = Application.objects.get(user=self.owner)
        self.assertEqual(app.status, "drafted")
        self.assertEqual(list(app.events.values_list("after", flat=True)), ["drafted"])
        self.assertRedirects(self.move(app, "applied"), reverse("application_board"))
        app.refresh_from_db()
        self.assertEqual((app.status, app.version), ("applied", 1))
        self.assertEqual(list(app.events.order_by("pk").values_list("before", "after")), [
            ("", "drafted"), ("drafted", "applied"),
        ])
        self.assertContains(self.client.get(reverse("application_board")), "Example Labs")

    def test_other_users_cannot_read_or_modify_records(self):
        self.create()
        app = Application.objects.get(user=self.owner)
        foreign = Client()
        foreign.force_login(self.other)
        self.assertNotContains(foreign.get(reverse("application_board")), "Example Labs")
        self.assertNotEqual(
            self.client.get(reverse("application_board_revision")).json()["revision"],
            foreign.get(reverse("application_board_revision")).json()["revision"],
        )
        self.assertEqual(self.move(app, "rejected", client=foreign).status_code, 404)
        app.refresh_from_db()
        self.assertEqual(app.status, "drafted")
        self.assertEqual(app.events.count(), 1)

    def test_stale_updates_and_replay_do_not_overwrite_or_duplicate_events(self):
        self.create()
        app = Application.objects.get(user=self.owner)
        other_tab = Client()
        other_tab.force_login(self.owner)
        before = other_tab.get(reverse("application_board_revision")).json()["revision"]
        self.move(app, "interview")
        self.assertEqual(self.move(app, "rejected").status_code, 409)
        app.refresh_from_db()
        self.assertRedirects(self.move(app, "interview"), reverse("application_board"))
        self.assertEqual(app.events.count(), 2)
        after = other_tab.get(reverse("application_board_revision")).json()["revision"]
        self.assertNotEqual(before, after)
        app.refresh_from_db()
        self.assertEqual(app.status, "interview")

    def test_invalid_input_and_private_board(self):
        self.assertEqual(self.create(source_url="javascript:alert(1)").status_code, 422)
        self.assertEqual(self.create(company=" ", role=" ").status_code, 422)
        self.assertFalse(Application.objects.exists())
        self.create(status="offer declined")
        self.assertEqual(Application.objects.count(), 0)
        self.create(status="offer_declined")
        app = Application.objects.get()
        self.assertEqual(self.move(app, "garbage").status_code, 422)
        self.assertEqual(self.move(app, "hired", version="-1").status_code, 422)
        self.assertEqual(app.events.count(), 1)
        guest = Client()
        for url in (reverse("application_board"), reverse("application_board_revision")):
            self.assertEqual(guest.get(url).status_code, 302)
        self.assertEqual(guest.post(reverse("application_create"), {}).status_code, 302)
        self.assertEqual(self.client.get(reverse("application_create")).status_code, 405)
        self.assertEqual(self.client.get(reverse("application_move", args=[app.pk])).status_code, 405)

    def test_status_values_match_canonical_tracker_vocabulary(self):
        self.assertEqual(set(ApplicationStatus.values), {
            "drafted", "applied", "interview", "offer", "hired",
            "rejected", "no_response", "offer_declined", "withdrawn",
        })

    def test_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(reverse("application_create"), {
            "company": "Example Labs", "role": "Engineer", "status": "drafted",
        }).status_code, 403)



class LegacyTrackerImportTests(TestCase):
    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.upload_class = SimpleUploadedFile
        self.user = get_user_model().objects.create_user("legacy_importer", password="legacy-test-2026")
        self.client.force_login(self.user)
        self.header = (
            "date,company,sector,role,role_type,channel,status,contact_person,fit_rating,"
            "notes,cv_file,cover_letter_file,source,deadline"
        )
        self.lines = [
            "2026-10-01,Example Labs,tech,Data Engineer,full_time,portal,applied,,8,,cv/example.tex,,linkedin,",
            "2026-10-05,Second Co,tech,Analyst,full_time,email,offer declined,,7,,,,naukri,",
            "2026-10-06,Third Co,tech,Manager,full_time,email,no response,,7,,,,naukri,",
        ]

    def upload(self, lines=None, other=None):
        payload = (self.header + "\n" + "\n".join(self.lines if lines is None else lines) + "\n").encode()
        return (other or self.client).post(
            reverse("application_import"),
            {"tracker_csv": self.upload_class("job_search_tracker.csv", payload, content_type="text/csv")},
        )

    def test_import_normalizes_legacy_status_and_keeps_source_date(self):
        self.assertRedirects(self.upload(), reverse("application_board"))
        self.assertEqual(Application.objects.filter(user=self.user).count(), 3)
        self.assertEqual(set(Application.objects.values_list("status", flat=True)),
                         {"applied", "offer_declined", "no_response"})
        self.assertEqual(Application.objects.get(company="Example Labs").tracker_date.isoformat(), "2026-10-01")
        self.assertEqual(ApplicationStatusEvent.objects.filter(origin="tracker_import").count(), 3)

    def test_repeated_import_never_overwrites_manual_stage(self):
        self.upload()
        app = Application.objects.get(company="Example Labs")
        self.assertRedirects(
            self.client.post(reverse("application_move", args=[app.pk]), {
                "status": "interview", "version": 0,
            }), reverse("application_board"))
        self.assertRedirects(self.upload(), reverse("application_board"))
        app.refresh_from_db()
        self.assertEqual((app.status, app.version), ("interview", 1))
        self.assertEqual(Application.objects.count(), 3)
        self.assertEqual(app.events.count(), 2)

    def test_reject_invalid_csv_atomically(self):
        invalid = self.lines + ["2026-10-07,Bad Co,tech,Engineer,full_time,portal,fake,,8,,,,,",]
        self.assertEqual(self.upload(invalid).status_code, 422)
        self.assertEqual(Application.objects.count(), 0)
        self.assertEqual(self.upload(["wrong,columns"]).status_code, 422)
        self.assertEqual(Application.objects.count(), 0)
        self.assertEqual(self.client.get(reverse("application_import")).status_code, 405)
        self.assertEqual(Client().post(reverse("application_import"), {}).status_code, 302)

    def test_import_isolated_from_other_users_and_updates_revision(self):
        visitor = Client()
        other = get_user_model().objects.create_user("legacy_other", password="legacy-other-2026")
        visitor.force_login(other)
        before = self.client.get(reverse("application_board_revision")).json()["revision"]
        self.assertRedirects(self.upload(), reverse("application_board"))
        after = self.client.get(reverse("application_board_revision")).json()["revision"]
        self.assertNotEqual(before, after)
        self.assertNotContains(visitor.get(reverse("application_board")), "Example Labs")
        self.assertRedirects(self.upload(other=visitor), reverse("application_board"))
        self.assertEqual(Application.objects.filter(user=other).count(), 3)

    def test_explicit_offer_decision_is_required_and_audited(self):
        self.upload()
        app = Application.objects.get(company="Example Labs")
        endpoint = reverse("application_move", args=[app.pk])
        self.assertEqual(self.client.post(endpoint, {"status": "hired", "version": 0}).status_code, 422)
        self.assertRedirects(
            self.client.post(endpoint, {"status": "hired", "version": 0,
                                       "confirm_offer_decision": "yes"}), reverse("application_board"))
        self.assertEqual(app.events.order_by("-id").first().origin, "manual_offer_decision")



class AuditOriginSchemaTests(TestCase):
    def test_longest_origin_label_fits_strict_database_column(self):
        # SQLite accepts oversized varchar values; PostgreSQL would reject the insert.
        # Keep this assertion independent of backend-specific varchar enforcement.
        origin = ApplicationStatusEvent._meta.get_field("origin")
        self.assertGreaterEqual(origin.max_length, len("manual_offer_decision"))
        self.assertGreaterEqual(origin.max_length, len("tracker_import"))
