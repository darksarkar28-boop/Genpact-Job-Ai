from django.conf import settings
from django.db import models


class Source(models.Model):
    key = models.CharField(max_length=160, unique=True)
    provider = models.CharField(max_length=32)
    label = models.CharField(max_length=200)
    enabled = models.BooleanField(default=True)
    status = models.CharField(max_length=32, default="pending")
    last_attempt = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    invalid_count = models.PositiveIntegerField(default=0)


class Posting(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="postings")
    source_id = models.TextField()
    employer = models.TextField()
    title = models.TextField()
    location = models.TextField(blank=True)
    url = models.TextField()
    region = models.CharField(max_length=8)
    description = models.TextField(blank=True)
    legacy_key = models.CharField(max_length=160)
    posted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(db_index=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "source_id"], name="jobfeeds_unique_source_job")]


class Preference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    market = models.CharField(max_length=8, default="all")
    role = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    zone = models.CharField(max_length=80, default="Asia/Kolkata")
    hour = models.PositiveSmallIntegerField(default=8)
    email = models.EmailField(blank=True)
    verified = models.BooleanField(default=False)
    opted_in = models.BooleanField(default=False)
    verification_sent = models.DateTimeField(null=True, blank=True)
    nonce = models.CharField(max_length=64, blank=True)


class Delivery(models.Model):
    preference = models.ForeignKey(Preference, on_delete=models.CASCADE)
    local_date = models.DateField()
    status = models.CharField(max_length=16, default="claimed")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["preference", "local_date"], name="jobfeeds_one_digest_per_day")]
