"""CI runs this race on a file-backed SQLite database, as in deployment."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase

from .models import Membership, Organization, Role
from .services import CampusError, invite_batch, seats_used


class LastSeatTests(TransactionTestCase):
    def test_two_simultaneous_batches_cannot_reserve_the_same_last_seat(self):
        org = Organization.objects.create(name="One seat", slug="one-seat", seat_limit=1)
        cohort = org.cohorts.create(name="Engineering")
        admin = get_user_model().objects.create_user("org_admin")
        Membership.objects.create(organization=org, role=Role.ADMIN, user=admin)
        start = Barrier(2)

        def reserve(email):
            try:
                start.wait(timeout=10)
                invite_batch(org.pk, admin, [email], Role.CANDIDATE, cohort.pk)
                return "reserved"
            except CampusError:
                return "full"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(reserve, ["one@example.test", "two@example.test"]))
        self.assertCountEqual(results, ["reserved", "full"])
        self.assertEqual(seats_used(org), 1)
        self.assertEqual(org.invitations.count(), 1)
