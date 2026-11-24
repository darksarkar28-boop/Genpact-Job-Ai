"""Account-owned mailbox connect and disconnect actions. Sync is separate."""
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .mail_oauth import MailError, PROVIDERS, complete_authorization, start_authorization
from .models import Application, ApplicationStatusEvent, MailConnection, MailSignal
from .mail_providers import load_messages
from .mail_classifier import import_signals


@login_required
@never_cache
@require_POST
def connect(request, provider):
    if provider not in PROVIDERS:
        return HttpResponseBadRequest("Unsupported provider.")
    try:
        return redirect(start_authorization(request, provider))
    except MailError as exc:
        messages.error(request, str(exc))
        return redirect("application_board")


@login_required
@never_cache
@require_GET
def callback(request, provider):
    if provider not in PROVIDERS:
        return HttpResponseBadRequest("Unsupported provider.")
    try:
        complete_authorization(request, provider)
    except MailError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Mailbox connected with read-only permissions.")
    return redirect("application_board")


@login_required
@never_cache
@require_POST
def disconnect(request, provider):
    if provider not in PROVIDERS:
        return HttpResponseBadRequest("Unsupported provider.")
    MailConnection.objects.filter(user=request.user, provider=provider).delete()
    request.session.pop(f"mail_oauth_{provider}", None)
    messages.success(request, "Mailbox disconnected. Local credentials and proposals deleted.")
    return redirect("application_board")



@login_required
@never_cache
@require_POST
def sync(request, provider):
    if provider not in PROVIDERS:
        return HttpResponseBadRequest("Unsupported provider.")
    connection = get_object_or_404(MailConnection, user=request.user, provider=provider)
    if not Application.objects.filter(user=request.user).exists():
        messages.error(request, "Add an application before syncing mailbox messages.")
        return redirect("application_board")
    try:
        found = import_signals(connection, load_messages(connection, request))
    except MailError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Mailbox checked. {found} new messages classified for review.")
    return redirect("application_board")


@login_required
@never_cache
@require_POST
def decide(request, signal_id):
    signal = get_object_or_404(
        MailSignal.objects.select_related("connection", "application"),
        pk=signal_id, connection__user=request.user,
    )
    action = request.POST.get("action")
    if action not in ("approve", "dismiss"):
        return HttpResponseBadRequest("Invalid decision.")
    if signal.state not in ("pending", "needs_review"):
        return HttpResponse("This message has already been handled.", status=409)
    if action == "dismiss":
        updated = MailSignal.objects.filter(pk=signal.pk, state=signal.state).update(
            state="dismissed", decided_at=timezone.now())
        if not updated:
            return HttpResponse("Decision was changed by another session.", status=409)
        return redirect("application_board")
    if signal.state != "pending" or not signal.application or not signal.proposed_status:
        return HttpResponse("This message requires manual review; no automatic proposal is available.", status=409)
    app = signal.application
    if app.user_id != request.user.pk:
        return HttpResponse("This application is not owned by your account.", status=403)
    # Compare-and-swap protects manual edits and other email approvals from stale writes.
    with transaction.atomic():
        current = Application.objects.filter(
            pk=app.pk, user=request.user, version=signal.base_version, status=app.status,
        ).update(status=signal.proposed_status, version=F("version") + 1, updated_at=timezone.now())
        if not current:
            MailSignal.objects.filter(pk=signal.pk, state="pending").update(
                state="stale", reason="Application changed since this email was reviewed.")
            return HttpResponse("Application changed. Review its current stage before continuing.", status=409)
        claimed = MailSignal.objects.filter(pk=signal.pk, state="pending").update(
            state="approved", decided_at=timezone.now())
        if not claimed:
            # Concurrent approval; roll back the tentative application update.
            transaction.set_rollback(True)
            return HttpResponse("Proposal was handled by another session.", status=409)
        ApplicationStatusEvent.objects.create(
            application=app, before=app.status, after=signal.proposed_status,
            origin=signal.connection.provider,
        )
    messages.success(request, "Approved change saved to your application history.")
    return redirect("application_board")
