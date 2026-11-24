from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from campus.models import Membership, Organization, Role
from campus.services import audit, locked_organization, seats_used


class Command(BaseCommand):
    help = "Provision or update a campus license and grant an existing account organization-admin access."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--seats", required=True, type=int)
        parser.add_argument("--admin", required=True)

    def handle(self, *args, **opts):
        user = get_user_model().objects.filter(username=opts["admin"], is_active=True).first()
        if user is None:
            raise CommandError("Choose an existing active admin account.")
        proposed = Organization(slug=opts["slug"], name=opts["name"].strip(), seat_limit=opts["seats"])
        try:
            proposed.full_clean(validate_unique=False)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        with transaction.atomic():
            Organization.objects.bulk_create([proposed], ignore_conflicts=True)
            org_id = Organization.objects.values_list("pk", flat=True).get(slug=opts["slug"])
            with locked_organization(org_id) as org:
                if opts["seats"] < seats_used(org):
                    raise CommandError("The seat limit must cover active candidates and reserved invitations.")
                member, created = Membership.objects.get_or_create(
                    organization=org, user=user, defaults={"role": Role.ADMIN, "contact_email": user.email.casefold()},
                )
                if member.role != Role.ADMIN:
                    raise CommandError("The selected account already has another role in this organization.")
                if created or not member.active:
                    member.active = True
                    member.save(update_fields=["active"])
                    audit(org, None, "Granted organization admin access", user.username)
                if created or org.seat_limit != opts["seats"] or org.name != proposed.name:
                    org.name, org.seat_limit = proposed.name, opts["seats"]
                    org.save(update_fields=["name", "seat_limit"])
                    audit(org, None, "Provisioned license", f"{org.seat_limit} candidate seats")
        self.stdout.write(f"{org.name}: {org.seat_limit} candidate seats. Admin: {user.username}.")
