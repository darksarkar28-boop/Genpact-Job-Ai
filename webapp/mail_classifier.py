"""Conservative employer matching and status proposals, never instructions from mail."""
import re

from django.db import transaction
from django.utils import timezone

from .models import Application, MailSignal

FINAL = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}
PROGRESS = {"drafted": 0, "applied": 1, "interview": 2, "offer": 3}


def _normalized(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _company_alias(company):
    name = _normalized(company)
    return re.sub(r"(?: inc| ltd| llc| corp| corporation| limited| pvt| group)+$", "", name)


def match_application(msg, applications):
    # Only subject, sender and the opening of the message count as employer evidence.
    source = _normalized(" ".join((msg["subject"], msg["sender"], msg["body"][:350])))
    squeezed = source.replace(" ", "")
    matches = []
    for app in applications:
        employer = _company_alias(app.company)
        if len(employer) < 4:
            continue
        if re.search(r"\b" + re.escape(employer) + r"\b", source) or (
            len(employer.replace(" ", "")) >= 7 and employer.replace(" ", "") in squeezed
        ):
            matches.append(app)
    if len(matches) == 1:
        return matches[0], ""
    return None, "More than one matching application" if matches else "No clearly matching employer"


def proposed_stage(msg, app):
    content = _normalized(msg["subject"] + " " + msg["body"][:650])
    if re.search(r"\b(not selected|other candidates|not moving forward|not proceeding|unable to proceed|regret to inform|unsuccessful application)\b", content):
        target, description = "rejected", "Rejection message"
    elif re.search(r"\b(offer letter|extend you an offer|pleased to offer|job offer|formal offer)\b", content):
        target, description = "offer", "Offer received (acceptance is always manual)"
    elif re.search(r"\b(online assessment|coding challenge|technical interview|interview invitation|schedule (?:a|your|an) (?:call|interview)|phone screen|hacker rank|hackerrank|codility|next round|interview slot)\b", content):
        target, description = "interview", "Assessment or interview invitation"
    elif app.status == "drafted" and re.search(r"\b(received your application|thank you for applying|application has been received|successfully submitted your application)\b", content):
        target, description = "applied", "Submission acknowledgment"
    else:
        return "", "No reliable status change identified"
    if app.status in FINAL:
        return "", "Conflicts with an already closed application"
    if target == app.status:
        return "", "Application already in this stage"
    if target in PROGRESS and PROGRESS[target] < PROGRESS.get(app.status, 0):
        return "", "Older or conflicting stage signal"
    return target, description


def import_signals(connection, messages):
    applications = list(Application.objects.filter(user=connection.user))
    new = 0
    for msg in messages:
        msg_id = str(msg.get("id", ""))
        if not msg_id or len(msg_id) > 512:
            continue
        if MailSignal.objects.filter(connection=connection, provider_message_id=msg_id).exists():
            continue
        app, match_reason = match_application(msg, applications)
        stage, reason = proposed_stage(msg, app) if app else ("", match_reason)
        state = "pending" if stage else ("needs_review" if app is None or "Conflicts" in reason or "conflicting" in reason else "ignored")
        # Never store message body or an access token.
        with transaction.atomic():
            signal, created = MailSignal.objects.get_or_create(
                connection=connection, provider_message_id=msg_id,
                defaults={
                    "application": app,
                    "subject": msg.get("subject", "")[:300],
                    "sender": msg.get("sender", "")[:250],
                    "source_link": msg.get("link", ""),
                    "received_at": msg.get("received_at"),
                    "proposed_status": stage,
                    "base_version": app.version if app else None,
                    "reason": reason, "state": state,
                },
            )
            if not created:
                continue
            new += 1
            if app and stage:
                conflicts = MailSignal.objects.filter(
                    application=app, state="pending",
                ).exclude(pk=signal.pk).exclude(proposed_status=stage)
                if conflicts.exists():
                    conflicts.update(state="needs_review", reason="Conflicting mailbox signals; decide manually.")
                    signal.state = "needs_review"
                    signal.reason = "Conflicting mailbox signals; decide manually."
                    signal.save(update_fields=["state", "reason"])
    connection.last_sync_at = timezone.now()
    connection.save(update_fields=["last_sync_at"])
    return new
