"""Verified opt-in email and durable at-most-once daily delivery attempts."""
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.urls import reverse
from django.utils import timezone

from .models import Delivery, Preference
from .services import local_clock, ranked


def send_email(address, subject, body):
    if not settings.JOBFEEDS_MAIL_ENABLED:
        raise ValueError("Job digest SMTP is not configured")
    connection = get_connection(
        backend=settings.JOBFEEDS_EMAIL_BACKEND,
        host=settings.JOBFEEDS_SMTP_HOST, port=settings.JOBFEEDS_SMTP_PORT,
        username=settings.JOBFEEDS_SMTP_USER, password=settings.JOBFEEDS_SMTP_PASSWORD,
        use_tls=settings.JOBFEEDS_SMTP_TLS, timeout=10,
    )
    if EmailMessage(subject, body, settings.JOBFEEDS_FROM_EMAIL, [address], connection=connection).send() != 1:
        raise OSError("email provider did not accept message")


def deliver_due(now=None, sender=None):
    now = now or timezone.now()
    sender = sender or send_email
    if not settings.JOBFEEDS_MAIL_ENABLED:
        return []
    results = []
    for pref in Preference.objects.filter(opted_in=True, verified=True).exclude(email="").iterator():
        local = local_clock(pref.zone, now)
        if local is None or not pref.hour <= local.hour < 12:
            continue
        # Unique DB constraint makes repeat/overlapping scheduler invocations safe.
        delivery, created = Delivery.objects.get_or_create(preference=pref, local_date=local.date())
        if not created:
            continue
        original_email = pref.email
        pref.refresh_from_db()
        current_local = local_clock(pref.zone, now)
        if (not pref.opted_in or not pref.verified or pref.email != original_email or current_local is None
                or current_local.date() != local.date() or not pref.hour <= current_local.hour < 12):
            delivery.status = "cancelled"
        else:
            try:
                matches = ranked(pref, now=now, limit=10)
                lines = ["Your ranked job matches (confirmed-profile keyword coverage, not hiring probability):", ""]
                for item in matches:
                    job = item["job"]
                    score = "Not available" if item["score"] is None else f'{item["score"]}%'
                    lines.extend([f"{job.title} — {job.employer} — {job.location}", f"Source: {job.source.provider}; coverage: {score}", job.url, ""])
                if not matches:
                    lines.append("No current matches. Check your filters or provider availability.")
                lines.extend(["", "To opt out, sign in to Genpact Job AI and uncheck the daily digest on Job feeds."])
                # Recheck consent immediately before the external side effect.
                if not Preference.objects.filter(pk=pref.pk, email=original_email, verified=True, opted_in=True).exists():
                    delivery.status = "cancelled"
                else:
                    sender(original_email, "Your morning job digest", "\n".join(lines))
                    delivery.status = "sent"
            except Exception:
                # Never retry an ambiguous SMTP result automatically: the mail
                # server may have accepted it before a transport failure.
                delivery.status = "failed"
        delivery.save(update_fields=["status"])
        results.append((delivery.pk, delivery.status))
    return results
