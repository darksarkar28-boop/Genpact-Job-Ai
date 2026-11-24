from django.core.management.base import BaseCommand, CommandError
from jobfeeds.mail import deliver_due


class Command(BaseCommand):
    help = "Attempt verified, opted-in daily job digests due in each local morning."

    def handle(self, *args, **options):
        results = deliver_due()
        self.stdout.write(f"{len(results)} digest attempts")
        if any(status == "failed" for _, status in results):
            raise CommandError("One or more email attempts failed; inspect delivery status before manual recovery.")
