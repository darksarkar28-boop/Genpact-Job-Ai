from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256

from django.core import signing
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import AuditEvent, Cohort, Feedback, Invitation, Membership, Organization, ResumeReview, Revision, Role


class CampusError(ValueError):
    pass


@contextmanager
def locked_organization(org_id):
    with transaction.atomic():
        # A write before any reads serializes the whole seat decision on SQLite
        # as well as databases with row locks. SELECT FOR UPDATE alone does not.
        if not Organization.objects.filter(pk=org_id).update(seat_limit=F("seat_limit")):
            raise Http404
        yield Organization.objects.get(pk=org_id)


def member_for(org, user, *roles):
    members = org.members.filter(user=user, active=True).select_related("cohort", "organization", "user")
    if roles:
        members = members.filter(role__in=roles)
    return get_object_or_404(members)


def audit(org, actor, action, subject):
    AuditEvent.objects.create(organization=org, actor=actor, action=action, subject=str(subject))


def pending(org):
    return org.invitations.filter(accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now())


def seats_used(org):
    return org.members.filter(active=True, role=Role.CANDIDATE).count() + pending(org).filter(role=Role.CANDIDATE).count()


def invite_token(invite):
    return signing.Signer(salt="campus.invitation.v1").sign(str(invite.pk))


def invitation_id(token):
    try:
        return signing.Signer(salt="campus.invitation.v1").unsign(token)
    except signing.BadSignature:
        raise Http404 from None


def invite_batch(org_id, actor, emails, role, cohort_id):
    with locked_organization(org_id) as org:
        member_for(org, actor, Role.ADMIN)
        cohort = get_object_or_404(org.cohorts, pk=cohort_id) if role != Role.ADMIN else None
        open_invites = {item.email: item for item in pending(org).filter(email__in=emails)}
        existing_members = set(org.members.filter(active=True, contact_email__in=emails).values_list("contact_email", flat=True))
        new_emails = []
        for email in emails:
            if email in existing_members:
                continue
            previous = open_invites.get(email)
            if previous is None:
                new_emails.append(email)
            elif previous.role != role or previous.cohort_id != (cohort.pk if cohort else None):
                raise CampusError(f"{email} has a different pending invitation. Revoke it before changing the assignment.")
        if role == Role.CANDIDATE and len(new_emails) + seats_used(org) > org.seat_limit:
            raise CampusError("There are too few available seats for this batch. Release a seat or invite fewer candidates.")
        created = []
        expires = timezone.now() + timedelta(days=7)
        for email in new_emails:
            invite = Invitation.objects.create(organization=org, cohort=cohort, role=role, email=email, expires_at=expires)
            audit(org, actor, f"Invited {role}", email)
            created.append(invite)
        return created


def accept_invitation(invite_id, user):
    invitation = get_object_or_404(Invitation, pk=invite_id)
    with locked_organization(invitation.organization_id) as org:
        invitation.refresh_from_db()
        if invitation.accepted_at and invitation.accepted_by_id == user.pk:
            return member_for(org, user)
        if invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= timezone.now():
            raise CampusError("This invitation is no longer available.")
        member = org.members.filter(user=user).first()
        if member and member.active:
            raise CampusError("This account already belongs to the organization. Ask the admin to update its assignment.")
        if member is None:
            member = Membership(organization=org, user=user)
        member.role = invitation.role
        member.contact_email = invitation.email
        member.cohort = invitation.cohort if member.role == Role.CANDIDATE else None
        member.active = True
        member.applications = member.interviews = member.offers = 0
        member.reported_at = None
        member.save()
        member.mentor_cohorts.set([invitation.cohort] if member.role == Role.MENTOR else [])
        invitation.accepted_at, invitation.accepted_by = timezone.now(), user
        invitation.save(update_fields=["accepted_at", "accepted_by"])
        audit(org, user, f"Accepted {member.role} invitation", invitation.email)
        return member


def revoke_invitation(org_id, actor, invite_id):
    with locked_organization(org_id) as org:
        member_for(org, actor, Role.ADMIN)
        invite = get_object_or_404(pending(org), pk=invite_id)
        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at"])
        audit(org, actor, "Revoked invitation", invite.email)


def close_reviews(member, actor):
    for review in member.reviews.filter(withdrawn_at__isnull=True):
        review.withdrawn_at = timezone.now()
        review.save(update_fields=["withdrawn_at"])
        audit(member.organization, actor, "Withdrew résumé access", f"Review {review.pk}")


def change_membership(org_id, actor, target_id, action, cohort_ids=()):
    with locked_organization(org_id) as org:
        member_for(org, actor, Role.ADMIN)
        target = get_object_or_404(org.members.select_related("user"), pk=target_id, active=True)
        if action == "deactivate":
            if target.role == Role.ADMIN and org.members.filter(active=True, role=Role.ADMIN).count() == 1:
                raise CampusError("The organization needs an active admin.")
            close_reviews(target, actor)
            target.active = False
            target.save(update_fields=["active"])
            target.mentor_cohorts.clear()
            audit(org, actor, "Deactivated membership", target.user.username)
            return
        cohorts = list(org.cohorts.filter(pk__in=cohort_ids))
        if len(cohorts) != len(set(cohort_ids)):
            raise Http404
        if action == "assign" and target.role == Role.MENTOR:
            target.mentor_cohorts.set(cohorts)
        elif action == "assign" and target.role == Role.CANDIDATE and len(cohorts) == 1:
            if target.cohort_id == cohorts[0].pk:
                return
            close_reviews(target, actor)
            target.cohort = cohorts[0]
            target.save(update_fields=["cohort"])
        else:
            raise CampusError("Choose a valid cohort assignment for this member.")
        audit(org, actor, "Changed cohort access", f"{target.user.username} → {', '.join(str(c.pk) for c in cohorts) or 'none'}")


def progress_counts(candidates):
    return candidates.aggregate(
        total=Count("pk"), onboarded=Count("pk", filter=Q(user__profile__revision__gt=0)),
        reporting=Count("pk", filter=Q(reported_at__isnull=False)),
        applied=Count("pk", filter=Q(applications__gt=0)), interviewed=Count("pk", filter=Q(interviews__gt=0)),
        offered=Count("pk", filter=Q(offers__gt=0)),
    )


def visible_reviews(member):
    reviews = ResumeReview.objects.filter(owner__organization=member.organization)
    if member.role == Role.CANDIDATE:
        return reviews.filter(owner=member)
    if member.role == Role.MENTOR:
        return reviews.filter(owner__active=True, owner__cohort_id=F("cohort_id"), withdrawn_at__isnull=True,
                              cohort__in=member.mentor_cohorts.all())
    return reviews.none()


def submit_resume(org_id, user, filename, document, expected_review, expected_revision):
    digest = sha256(document).hexdigest()
    with locked_organization(org_id) as org:
        member = member_for(org, user, Role.CANDIDATE)
        review = member.reviews.filter(withdrawn_at__isnull=True).first()
        latest = review.revisions.only("number", "sha256").first() if review else None
        if latest and latest.sha256 == digest and expected_review in (0, review.pk):
            return review
        if (review.pk if review else 0, latest.number if latest else 0) != (expected_review, expected_revision):
            raise CampusError("Another tab changed this review. Reload before sharing a revision.")
        if review is None:
            review = ResumeReview.objects.create(owner=member, cohort=member.cohort)
            audit(org, user, "Shared résumé with cohort mentors", f"Review {review.pk}, cohort {member.cohort_id}")
        revision = Revision.objects.create(review=review, number=expected_revision + 1, filename=filename,
                                           document=document, sha256=digest)
        audit(org, user, "Submitted résumé revision", f"Review {review.pk}, revision {revision.number}")
        return review


def add_feedback(org_id, user, review_id, revision_id, body):
    with locked_organization(org_id) as org:
        mentor = member_for(org, user, Role.MENTOR)
        review = get_object_or_404(visible_reviews(mentor), pk=review_id)
        revision = get_object_or_404(review.revisions.defer("document"), pk=revision_id)
        Feedback.objects.create(revision=revision, mentor=mentor, body=body)
        audit(org, user, "Commented on résumé", f"Review {review.pk}, revision {revision.number}")
