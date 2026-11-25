from django.conf import settings
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class DocumentJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    class DocumentType(models.TextChoices):
        CV = "cv", "CV"
        COVER_LETTER = "cover_letter", "Cover letter"

    class OutputFormat(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="document_jobs")
    profile_revision = models.PositiveIntegerField()
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    output_format = models.CharField(max_length=8, choices=OutputFormat.choices)
    template_key = models.CharField(max_length=32)
    fingerprint = models.CharField(max_length=64, db_index=True)
    content_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempt = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    artifact_name = models.CharField(max_length=255, blank=True)
    artifact_sha256 = models.CharField(max_length=64, blank=True)
    verification = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "-created_at"], name="docjob_user_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user", "fingerprint"], name="docjob_user_fingerprint_uniq"),
        ]


class TemplateReview(models.Model):
    template_key = models.CharField(max_length=32, unique=True)
    reviewer_label = models.CharField(max_length=120)
    feedback = models.TextField()
    approved = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class CareerContext(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class MockInterview(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    context = models.JSONField(default=dict)
    questions = models.JSONField(default=list)
    profile_revision = models.PositiveIntegerField()
    question_index = models.PositiveIntegerField(default=0)
    deadline = models.DateTimeField()
    mode = models.CharField(max_length=10, default="text")
    recording_consent = models.BooleanField(default=False)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class MockAnswer(models.Model):
    interview = models.ForeignKey(MockInterview, on_delete=models.CASCADE, related_name="answers")
    question_index = models.PositiveIntegerField()
    transcript = models.TextField(blank=True)
    feedback = models.JSONField(default=dict)
    recording = models.BinaryField(default=bytes, blank=True, editable=False)
    media_type = models.CharField(max_length=32, blank=True)
    timed_out = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["interview", "question_index"], name="unique_mock_answer")]
