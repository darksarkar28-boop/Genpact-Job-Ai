from django.conf import settings
from django.db import models


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class ApplicationStatus(models.TextChoices):
    """The tracker CSV vocabulary defined in .claude/commands/outcome.md."""

    DRAFTED = "drafted", "Drafted"
    APPLIED = "applied", "Applied"
    INTERVIEW = "interview", "Interview"
    OFFER = "offer", "Offer"
    HIRED = "hired", "Hired"
    REJECTED = "rejected", "Rejected"
    NO_RESPONSE = "no_response", "No response"
    OFFER_DECLINED = "offer_declined", "Offer declined"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Application(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_applications")
    company = models.CharField(max_length=250)
    role = models.CharField(max_length=250)
    source_url = models.URLField(max_length=2048, blank=True)
    status = models.CharField(max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFTED)
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ApplicationStatusEvent(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    before = models.CharField(max_length=20, blank=True)
    after = models.CharField(max_length=20, choices=ApplicationStatus.choices)
    origin = models.CharField(max_length=16, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)
