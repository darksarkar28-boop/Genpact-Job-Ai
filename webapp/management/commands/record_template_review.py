from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from webapp.documents import TEMPLATE_BY_KEY
from webapp.models import TemplateReview


class Command(BaseCommand):
    help = "Record recruiter feedback for a built-in ATS template."

    def add_arguments(self, parser):
        parser.add_argument("--template", required=True)
        parser.add_argument("--reviewer", required=True)
        parser.add_argument("--feedback", required=True)
        parser.add_argument("--approved", action="store_true")

    def handle(self, *args, **options):
        key = options["template"]
        if key not in TEMPLATE_BY_KEY:
            raise CommandError(f"Unknown template: {key}")
        reviewer = options["reviewer"].strip()
        feedback = options["feedback"].strip()
        if not reviewer or not feedback:
            raise CommandError("Reviewer and feedback must both be non-empty.")
        review, _ = TemplateReview.objects.update_or_create(
            template_key=key,
            defaults={
                "reviewer_label": reviewer,
                "feedback": feedback,
                "approved": options["approved"],
                "reviewed_at": timezone.now(),
            },
        )
        state = "approved" if review.approved else "recorded (not approved)"
        self.stdout.write(self.style.SUCCESS(f"{key}: recruiter feedback {state}"))
