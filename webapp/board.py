"""Authenticated application Kanban. No inbox content or account data is shared."""
import hashlib

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .models import Application, ApplicationStatus, ApplicationStatusEvent, MailConnection, MailSignal
from .mail_oauth import configured
from .tracker_import import TrackerImportForm, import_tracker


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ("company", "role", "source_url", "status")
        labels = {"source_url": "Job posting URL (optional)"}

    def clean_company(self):
        value = self.cleaned_data["company"].strip()
        if not value:
            raise forms.ValidationError("Enter a company.")
        return value

    def clean_role(self):
        value = self.cleaned_data["role"].strip()
        if not value:
            raise forms.ValidationError("Enter a job title.")
        return value

    def clean_source_url(self):
        value = self.cleaned_data.get("source_url") or ""
        if value and not value.lower().startswith(("http://", "https://")):
            raise forms.ValidationError("Only HTTPS or HTTP posting URLs are supported.")
        return value


def _applications(user):
    return Application.objects.filter(user=user)


def _revision(user):
    """Private, deterministic snapshot token for cross-tab polling."""
    pairs = _applications(user).order_by("pk").values_list("pk", "version")
    application_data = ";".join(f"{pk}:{version}" for pk, version in pairs)
    signals = MailSignal.objects.filter(connection__user=user).order_by("pk").values_list("pk", "state")
    signal_data = ";".join(f"{pk}:{state}" for pk, state in signals)
    data = application_data + "|" + signal_data
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def _board(request, form=None, status=200):
    applications = list(
        _applications(request.user).prefetch_related("events").order_by("-updated_at", "-pk")
    )
    return render(request, "webapp/board.html", {
        "applications": applications,
        "statuses": ApplicationStatus.choices,
        "form": form if form is not None else ApplicationForm(),
        "tracker_import_form": TrackerImportForm(),
        "revision": _revision(request.user),
        "mail_connections": {c.provider: c for c in MailConnection.objects.filter(user=request.user)},
        "google_configured": configured("google"),
        "microsoft_configured": configured("microsoft"),
        "mail_signals": MailSignal.objects.filter(connection__user=request.user).select_related(
            "application", "connection").exclude(state="ignored").order_by("-created_at")[:30],
    }, status=status)


@login_required
@never_cache
@require_GET
def application_board(request):
    return _board(request)


@login_required
@never_cache
@require_GET
def application_board_revision(request):
    return JsonResponse({"revision": _revision(request.user)})


@login_required
@never_cache
@require_POST
def application_create(request):
    form = ApplicationForm(request.POST)
    if not form.is_valid():
        return _board(request, form, status=422)
    with transaction.atomic():
        application = form.save(commit=False)
        application.user = request.user
        application.save()
        ApplicationStatusEvent.objects.create(application=application, after=application.status)
    messages.success(request, "Application added to your board.")
    return redirect("application_board")


@login_required
@never_cache
@require_POST
def application_move(request, application_id):
    application = get_object_or_404(_applications(request.user), pk=application_id)
    target = request.POST.get("status", "")
    if target not in ApplicationStatus.values:
        return HttpResponse("Choose a valid tracker status.", status=422)
    try:
        version = int(request.POST.get("version", ""))
    except (ValueError, TypeError):
        return HttpResponse("A valid application version is required.", status=422)
    if version < 0:
        return HttpResponse("A valid application version is required.", status=422)
    if application.version != version:
        return HttpResponse("Another session updated this application. Refresh and try again.", status=409)
    if application.status == target:
        return redirect("application_board")
    if target in ("hired", "offer_declined") and request.POST.get("confirm_offer_decision") != "yes":
        return HttpResponse("Confirm your explicit decision to accept or decline this offer.", status=422)

    # The compare-and-swap update protects concurrent browser sessions, including SQLite.
    # A lost race must not create a misleading history event.
    with transaction.atomic():
        updated = _applications(request.user).filter(
            pk=application.pk, version=version, status=application.status,
        ).update(status=target, version=F("version") + 1, updated_at=timezone.now())
        if not updated:
            return HttpResponse("Application changed in another session. Refresh and retry.", status=409)
        ApplicationStatusEvent.objects.create(
            application=application, before=application.status, after=target,
            origin="manual_offer_decision" if target in ("hired", "offer_declined") else "manual",
        )
        MailSignal.objects.filter(application=application, state="pending").update(
            state="stale", reason="Stage updated manually; review this signal against the new stage.",
        )
    messages.success(request, "Stage updated and saved to history.")
    return redirect("application_board")



@login_required
@never_cache
@require_POST
def application_import(request):
    form = TrackerImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponse("Import a valid .csv tracker under 1 MiB.", status=422)
    try:
        created, skipped = import_tracker(request.user, form.cleaned_data["tracker_csv"])
    except ValueError as exc:
        return HttpResponse(str(exc), status=422, content_type="text/plain")
    messages.success(request, f"Imported {created} applications; skipped {skipped} previously imported rows.")
    return redirect("application_board")
