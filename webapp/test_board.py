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
