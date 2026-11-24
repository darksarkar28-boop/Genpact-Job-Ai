import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Role(models.TextChoices):
    ADMIN = "admin", "Organization admin"
    MENTOR = "mentor", "Mentor"
    CANDIDATE = "candidate", "Candidate"


class Organization(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    seat_limit = models.PositiveIntegerField()

    def __str__(self):
        return self.name


class Cohort(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="cohorts")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="campus_cohort_name")]

    def __str__(self):
        return self.name


class Membership(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campus_memberships")
    role = models.CharField(max_length=12, choices=Role)
    contact_email = models.EmailField(blank=True)
    active = models.BooleanField(default=True)
    cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT, related_name="members")
    mentor_cohorts = models.ManyToManyField(Cohort, blank=True, related_name="mentors")
    applications = models.PositiveIntegerField(default=0)
    interviews = models.PositiveIntegerField(default=0)
    offers = models.PositiveIntegerField(default=0)
    reported_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="campus_one_membership"),
            models.CheckConstraint(
                condition=Q(role=Role.CANDIDATE, cohort__isnull=False)
                | Q(role__in=[Role.ADMIN, Role.MENTOR], cohort__isnull=True),
                name="campus_candidate_has_cohort",
            ),
        ]


class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=12, choices=Role)
    cohort = models.ForeignKey(Cohort, null=True, on_delete=models.PROTECT)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    accepted_at = models.DateTimeField(null=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)


class ResumeReview(models.Model):
    owner = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="reviews")
    cohort = models.ForeignKey(Cohort, on_delete=models.PROTECT, related_name="reviews")
    shared_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["owner"], condition=Q(withdrawn_at__isnull=True), name="campus_one_open_review"
        )]


class Revision(models.Model):
    review = models.ForeignKey(ResumeReview, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    filename = models.CharField(max_length=150)
    document = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["review", "number"], name="campus_revision_number")]


class Feedback(models.Model):
    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="feedback")
    mentor = models.ForeignKey(Membership, on_delete=models.PROTECT)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class AuditEvent(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    subject = models.CharField(max_length=254)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]
