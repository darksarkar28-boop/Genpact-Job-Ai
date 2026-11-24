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


class MailConnection(models.Model):
    """Account-owned OAuth credentials, encrypted at rest."""
    PROVIDERS = (("google", "Gmail"), ("microsoft", "Outlook"))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_connections")
    provider = models.CharField(max_length=16, choices=PROVIDERS)
    access_token_ciphertext = models.TextField()
    refresh_token_ciphertext = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    needs_reconnect = models.BooleanField(default=False)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "provider"], name="mail_connection_per_user")]


class MailSignal(models.Model):
    """Single email source metadata and a proposed, never automatic, stage transition."""
    STATES = (
        ("pending", "Awaiting approval"), ("needs_review", "Needs manual review"),
        ("ignored", "No actionable change"), ("approved", "Approved"),
        ("dismissed", "Dismissed"), ("stale", "Stale"),
    )
    connection = models.ForeignKey(MailConnection, on_delete=models.CASCADE, related_name="signals")
    application = models.ForeignKey(Application, null=True, blank=True, on_delete=models.CASCADE, related_name="mail_signals")
    provider_message_id = models.CharField(max_length=512)
    subject = models.CharField(max_length=300, blank=True)
    sender = models.CharField(max_length=250, blank=True)
    source_link = models.URLField(max_length=2048, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    proposed_status = models.CharField(max_length=20, blank=True)
    base_version = models.PositiveIntegerField(null=True, blank=True)
    state = models.CharField(max_length=20, choices=STATES, default="ignored")
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "provider_message_id"], name="mail_message_once")]
