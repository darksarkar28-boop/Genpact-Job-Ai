import smtplib
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db.models import Prefetch
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import services
from .forms import BulkInviteForm, FeedbackForm, ProgressForm, ResumeForm
from .models import Feedback, Invitation, Organization, Role


def private(view):
    return login_required(never_cache(view))


def organization(slug):
    return get_object_or_404(Organization, slug=slug)


def problem(request, message, status=409):
    return render(request, "campus/problem.html", {"problem": message}, status=status)


def invite_url(request, invite):
    return request.build_absolute_uri(reverse("campus:invite", args=[services.invite_token(invite)]))


@private
@require_GET
def home(request):
    memberships = request.user.campus_memberships.filter(active=True).select_related("organization", "cohort")
    return render(request, "campus/home.html", {"memberships": memberships.order_by("organization__name")})


@never_cache
@require_GET
def open_invitation(request, token):
    try:
        invite_id = UUID(services.invitation_id(token))
    except ValueError:
        raise Http404 from None
    invite = get_object_or_404(Invitation.objects.select_related("organization"), pk=invite_id)
    if invite.accepted_at and request.user.is_authenticated and invite.accepted_by_id == request.user.pk:
        return redirect("campus:dashboard", slug=invite.organization.slug)
    if invite.accepted_at or invite.revoked_at or invite.expires_at <= timezone.now():
        return problem(request, "This invitation expired, was revoked, or has already been accepted.", 410)
    request.session["campus_invite"] = str(invite.pk)
    response = redirect("campus:join")
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def join(request):
    invite = get_object_or_404(Invitation.objects.select_related("organization", "cohort"),
                               pk=request.session.get("campus_invite"))
    if invite.revoked_at or invite.accepted_at or invite.expires_at <= timezone.now():
        return problem(request, "This invitation is no longer available.", 410)
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect("login")
        if request.POST.get("invitation") != str(invite.pk):
            return problem(request, "Another invitation was opened. Review the invitation again.")
        try:
            member = services.accept_invitation(invite.pk, request.user)
        except services.CampusError as exc:
            return problem(request, exc)
        request.session.pop("campus_invite", None)
        return redirect("campus:dashboard", slug=member.organization.slug)
    return render(request, "campus/join.html", {"invitation": invite})


def dashboard_data(request, org, member):
    data = {"org": org, "member": member}
    if member.role == Role.CANDIDATE:
        reviews = services.visible_reviews(member).order_by("-pk")
        current = reviews.filter(withdrawn_at__isnull=True).first()
        latest = current.revisions.only("number").first() if current else None
        data.update(reviews=reviews, progress_form=ProgressForm(initial={
            key: getattr(member, key) for key in ("applications", "interviews", "offers")
        }), resume_form=ResumeForm(initial={
            "current_review": current.pk if current else 0, "current_revision": latest.number if latest else 0,
            "current_cohort": member.cohort_id,
        }))
        return data
    cohorts = org.cohorts.all() if member.role == Role.ADMIN else member.mentor_cohorts.all()
    selected = None
    if raw := request.GET.get("cohort"):
        if not raw.isdecimal() or len(raw) > 18:
            raise Http404
        selected = get_object_or_404(cohorts, pk=int(raw))
    scope = [selected.pk] if selected else cohorts.values_list("pk", flat=True)
    candidates = org.members.filter(active=True, role=Role.CANDIDATE, cohort_id__in=scope)
    data.update(cohorts=cohorts, selected=selected, stats=services.progress_counts(candidates))
    if member.role == Role.MENTOR:
        data["reviews"] = services.visible_reviews(member).filter(cohort_id__in=scope).select_related("owner__user", "cohort")
    else:
        roster = org.members.select_related("user", "cohort").prefetch_related("mentor_cohorts").order_by("user__username")
        if selected:
            roster = roster.filter(pk__in=candidates) | roster.filter(active=True, role=Role.MENTOR, mentor_cohorts=selected)
        data.update(roster=roster.distinct(), used=services.seats_used(org), invite_form=BulkInviteForm(org),
                    invitations=[(inv, invite_url(request, inv)) for inv in services.pending(org).select_related("cohort").order_by("email")],
                    events=org.events.select_related("actor")[:50])
    return data


@private
@require_GET
def dashboard(request, slug):
    org = organization(slug)
    member = services.member_for(org, request.user)
    return render(request, "campus/dashboard.html", dashboard_data(request, org, member))


@private
@require_POST
def create_cohort(request, slug):
    with services.locked_organization(organization(slug).pk) as org:
        services.member_for(org, request.user, Role.ADMIN)
        name = request.POST.get("name", "").strip()
        if not name or len(name) > 100:
            return problem(request, "Enter a cohort name of 1 to 100 characters.", 422)
        if org.cohorts.filter(name__iexact=name).exists():
            return problem(request, "This cohort already exists.")
        org.cohorts.create(name=name)
        services.audit(org, request.user, "Created cohort", name)
    return redirect("campus:dashboard", slug=slug)


@private
@require_POST
def invite_members(request, slug):
    org = organization(slug)
    member = services.member_for(org, request.user, Role.ADMIN)
    form = BulkInviteForm(org, request.POST)
    if not form.is_valid():
        return render(request, "campus/dashboard.html", {**dashboard_data(request, org, member), "invite_form": form}, status=422)
    values = form.cleaned_data
    try:
        created = services.invite_batch(org.pk, request.user, values["emails"], values["role"],
                                        values["cohort"].pk if values["cohort"] else None)
    except services.CampusError as exc:
        return problem(request, exc)
    failed = []
    if values.get("deliver"):
        for invite in created:
            try:
                count = send_mail(f"Invitation to {org.name}", f"Join {org.name} as {invite.get_role_display()}.\n\n"
                                  f"{invite_url(request, invite)}\n\nThis private invitation expires in seven days.",
                                  settings.DEFAULT_FROM_EMAIL, [invite.email])
                if not count:
                    failed.append(invite.email)
            except (OSError, smtplib.SMTPException):
                failed.append(invite.email)
    messages.success(request, f"Created {len(created)} invitation(s). Existing assignments were preserved.")
    if failed:
        messages.warning(request, "Email delivery failed for " + ", ".join(failed) + ". Copy the private links below.")
    return redirect("campus:dashboard", slug=slug)


@private
@require_POST
def revoke(request, slug, invite_id):
    services.revoke_invitation(organization(slug).pk, request.user, invite_id)
    return redirect("campus:dashboard", slug=slug)


@private
@require_POST
def manage_member(request, slug, member_id):
    ids = request.POST.getlist("cohorts")
    if any(not value.isdecimal() or len(value) > 18 for value in ids):
        raise Http404
    try:
        services.change_membership(organization(slug).pk, request.user, member_id, request.POST.get("action"),
                                   [int(value) for value in ids])
    except services.CampusError as exc:
        return problem(request, exc)
    return redirect("campus:dashboard", slug=slug)


@private
@require_POST
def progress(request, slug):
    with services.locked_organization(organization(slug).pk) as org:
        member = services.member_for(org, request.user, Role.CANDIDATE)
        form = ProgressForm(request.POST)
        if not form.is_valid():
            return render(request, "campus/dashboard.html", {**dashboard_data(request, org, member), "progress_form": form}, status=422)
        org.members.filter(pk=member.pk).update(**form.cleaned_data, reported_at=timezone.now())
    messages.success(request, "Your cohort progress has been saved.")
    return redirect("campus:dashboard", slug=slug)


@private
@require_POST
def share(request, slug):
    org = organization(slug)
    member = services.member_for(org, request.user, Role.CANDIDATE)
    form = ResumeForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "campus/dashboard.html", {**dashboard_data(request, org, member), "resume_form": form}, status=422)
    name, data = form.cleaned_data["document"]
    try:
        review = services.submit_resume(org.pk, request.user, name, data,
                                        form.cleaned_data["current_review"], form.cleaned_data["current_revision"],
                                        form.cleaned_data["current_cohort"])
    except services.CampusError as exc:
        return problem(request, exc)
    return redirect("campus:review", slug=slug, review_id=review.pk)


@private
@require_POST
def withdraw(request, slug, review_id):
    with services.locked_organization(organization(slug).pk) as org:
        member = services.member_for(org, request.user, Role.CANDIDATE)
        get_object_or_404(member.reviews, pk=review_id, withdrawn_at__isnull=True)
        services.close_reviews(member, request.user)
    return redirect("campus:dashboard", slug=slug)


@private
@require_GET
def review_detail(request, slug, review_id):
    org = organization(slug)
    member = services.member_for(org, request.user)
    review = get_object_or_404(services.visible_reviews(member).select_related("owner__user", "cohort"), pk=review_id)
    if member.role == Role.MENTOR:
        services.audit(org, request.user, "Viewed shared review", f"Review {review.pk}")
    revisions = review.revisions.defer("document").prefetch_related(
        Prefetch("feedback", queryset=Feedback.objects.select_related("mentor__user")),
    )
    return render(request, "campus/review.html", {"org": org, "member": member, "review": review,
                                                  "revisions": revisions})


@private
@require_GET
def download(request, slug, review_id, revision_id):
    org = organization(slug)
    member = services.member_for(org, request.user)
    review = get_object_or_404(services.visible_reviews(member), pk=review_id)
    revision = get_object_or_404(review.revisions, pk=revision_id)
    services.audit(org, request.user, "Downloaded résumé", f"Review {review.pk}, revision {revision.number}")
    content_type = "application/pdf" if revision.filename.lower().endswith(".pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    response = HttpResponse(bytes(revision.document), content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(True, revision.filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@private
@require_POST
def comment(request, slug, review_id, revision_id):
    org = organization(slug)
    services.member_for(org, request.user, Role.MENTOR)
    form = FeedbackForm(request.POST)
    if not form.is_valid():
        return problem(request, "Enter a comment of 1 to 4,000 characters.", 422)
    services.add_feedback(org.pk, request.user, review_id, revision_id, form.cleaned_data["comment"])
    return redirect("campus:review", slug=slug, review_id=review_id)
