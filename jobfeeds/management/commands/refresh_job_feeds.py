from django.core.management.base import BaseCommand, CommandError
from jobfeeds.services import refresh


class Command(BaseCommand):
    help = "Refresh configured public and authorized job-feed snapshots."

    def handle(self, *args, **options):
        try:
            outcomes = refresh()
        except (ValueError, TypeError, KeyError):
            raise CommandError("Invalid JOBFEEDS_CONFIG; check operator configuration.") from None
        for key, status in outcomes:
            self.stdout.write(f"{key}: {status}")
        if any(status != "ok" for _, status in outcomes):
            raise CommandError("One or more sources were incomplete; independent results were retained.")
