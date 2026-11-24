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
from .models import Application, ApplicationStatus, ApplicationStatusEvent, MailConnection, MailSignal
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
    if action not in ("approve", "dismiss", "resolve"):
        return HttpResponseBadRequest("Invalid decision.")
    if signal.state not in ("pending", "needs_review"):
        return HttpResponse("This message has already been handled.", status=409)
    if action == "dismiss":
        updated = MailSignal.objects.filter(pk=signal.pk, state=signal.state).update(
            state="dismissed", decided_at=timezone.now())
        if not updated:
            return HttpResponse("Decision was changed by another session.", status=409)
        return redirect("application_board")
    if action == "resolve":
        return resolve_signal(request, signal)
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
            origin=signal.connection.provider, mail_signal=signal,
            source_message_id=signal.provider_message_id,
            source_message_link=signal.source_link,
        )
    messages.success(request, "Approved change saved to your application history.")
    return redirect("application_board")



def resolve_signal(request, signal):
    """Resolve an uncertain email with a per-card optimistic version check."""
    if signal.state != "needs_review":
        return HttpResponse("This message is not awaiting manual resolution.", status=409)
    if request.POST.get("confirm_resolution") != "yes":
        return HttpResponse("Confirm your manual email-to-application decision.", status=422)
    try:
        app_pk, version = request.POST.get("application_choice", "").split(":")
        app_pk = int(app_pk)
        version = int(version)
    except (ValueError, TypeError):
        return HttpResponse("Select a valid application and refresh if it changed.", status=422)
    if app_pk < 1 or version < 0:
        return HttpResponse("Select a valid application and version.", status=422)
    app = get_object_or_404(Application, pk=app_pk, user=request.user)
    target = request.POST.get("status", "")
    if target not in ApplicationStatus.values:
        return HttpResponse("Choose a valid stage.", status=422)
    if target in ("hired", "offer_declined") and request.POST.get("confirm_offer_decision") != "yes":
        return HttpResponse("Explicitly confirm offer acceptance or decline.", status=422)
    if target == app.status:
        return HttpResponse("Application is already at this stage. Dismiss this message instead.", status=422)
    if app.version != version:
        return HttpResponse("Application stage changed in another session. Reload and review again.", status=409)

    with transaction.atomic():
        claimed = MailSignal.objects.filter(
            pk=signal.pk, connection__user=request.user, state="needs_review",
        ).update(
            application=app, state="approved", proposed_status=target,
            base_version=version, decided_at=timezone.now(),
            reason="Resolved explicitly by candidate after uncertain or conflicting mailbox signal.",
        )
        if not claimed:
            return HttpResponse("This message was handled in another session.", status=409)
        updated = Application.objects.filter(
            pk=app.pk, user=request.user, status=app.status, version=version,
        ).update(status=target, version=F("version") + 1, updated_at=timezone.now())
        if not updated:
            transaction.set_rollback(True)
            return HttpResponse("Application changed; the email decision was not saved.", status=409)
        ApplicationStatusEvent.objects.create(
            application=app, before=app.status, after=target,
            origin="manual_mail_review", mail_signal=signal,
            source_message_id=signal.provider_message_id,
            source_message_link=signal.source_link,
        )
        MailSignal.objects.filter(application=app, state="pending").exclude(pk=signal.pk).update(
            state="needs_review", reason="Stage changed after this proposal; resolve against current status.",
        )
    messages.success(request, "Manual review saved with the source email in application history.")
    return redirect("application_board")
