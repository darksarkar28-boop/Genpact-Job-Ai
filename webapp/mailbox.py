"""Account-owned mailbox connect and disconnect actions. Sync is separate."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .mail_oauth import MailError, PROVIDERS, complete_authorization, start_authorization
from .models import MailConnection


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
