import time

from django.core.management.base import BaseCommand

from webapp.documents import claim_next_job, process_claimed_job


class Command(BaseCommand):
    help = "Process queued document exports in the isolated container worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit.")
        parser.add_argument("--poll-seconds", type=float, default=1.0)

    def handle(self, *args, **options):
        poll = max(0.1, options["poll_seconds"])
        while True:
            job = claim_next_job()
            if job is None:
                if options["once"]:
                    return
                time.sleep(poll)
                continue
            self.stdout.write(f"Processing document job {job.pk} (attempt {job.attempt})")
            process_claimed_job(job)
            job.refresh_from_db(fields=["status", "error"])
            if job.status == "succeeded":
                self.stdout.write(self.style.SUCCESS(f"Document job {job.pk} succeeded"))
            else:
                self.stderr.write(self.style.ERROR(f"Document job {job.pk} failed: {job.error}"))
            if options["once"]:
                return
