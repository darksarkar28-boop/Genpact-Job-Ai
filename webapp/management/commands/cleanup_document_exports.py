from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from webapp.documents import artifact_file
from webapp.models import DocumentJob


class Command(BaseCommand):
    help = "Delete old document artifacts while retaining their audit/job records."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, default=30)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        days = options["older_than_days"]
        if days < 1:
            raise CommandError("--older-than-days must be at least 1.")
        cutoff = timezone.now() - timedelta(days=days)
        jobs = DocumentJob.objects.exclude(
            status__in=[DocumentJob.Status.QUEUED, DocumentJob.Status.RUNNING]
        ).filter(finished_at__lt=cutoff).exclude(artifact_name="")

        cleaned = 0
        root = Path(settings.DOCUMENT_EXPORT_ROOT).resolve()
        for job in jobs.iterator():
            path = artifact_file(job)
            if options["dry_run"]:
                self.stdout.write(f"would clean job {job.pk}: {job.artifact_name}")
                cleaned += 1
                continue
            if path:
                path.unlink(missing_ok=True)
                parent = path.parent
                try:
                    if parent != root:
                        parent.rmdir()
                    user_dir = parent.parent
                    if user_dir != root:
                        user_dir.rmdir()
                except OSError:
                    pass
            verification = dict(job.verification or {})
            verification["artifact_cleaned_at"] = timezone.now().isoformat()
            job.artifact_name = ""
            job.artifact_sha256 = ""
            job.verification = verification
            job.save(update_fields=["artifact_name", "artifact_sha256", "verification", "updated_at"])
            cleaned += 1
        self.stdout.write(self.style.SUCCESS(f"cleaned {cleaned} document artifact(s)"))
