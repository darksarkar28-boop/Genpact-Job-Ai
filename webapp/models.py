from django.conf import settings
from django.db import models


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    data = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
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
