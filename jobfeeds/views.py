from datetime import timedelta
from secrets import token_hex

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from .forms import PreferencesForm
from .mail import send_email
from .models import Delivery, Preference
from .services import provider_status, ranked

SALT = "jobfeeds-email-verification-v1"


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def home(request):
    pref, _ = Preference.objects.get_or_create(user=request.user)
    form = PreferencesForm(request.POST if request.method == "POST" else None,
                           initial={name: getattr(pref, name) for name in PreferencesForm.base_fields})
    status = 200
    if request.method == "POST":
        if form.is_valid():
            values = form.cleaned_data
            # Do not let concurrent preference requests resurrect old verification.
            with transaction.atomic():
                Preference.objects.filter(pk=pref.pk).update(market=values["market"])
                pref.refresh_from_db()
                changed = pref.email != values["email"]
                if changed:
                    pref.verified = False
                    pref.nonce = ""
                    # Retain verification cooldown even when recipient changes.
                for name in ("role", "location", "zone", "hour", "email"):
                    setattr(pref, name, values[name])
                pref.opted_in = bool(values["opted_in"] and pref.verified and pref.email)
                pref.save()
            if values["opted_in"] and not pref.opted_in:
                messages.info(request, "Verify this email first, then enable the digest.")
            else:
                messages.success(request, "Job preferences saved.")
            return redirect("jobfeeds:home")
        status = 422
    return render(request, "jobfeeds/home.html", {
        "form": form, "preference": pref, "matches": ranked(pref), "providers": provider_status(),
        "mail_enabled": settings.JOBFEEDS_MAIL_ENABLED,
        "deliveries": Delivery.objects.filter(preference=pref).order_by("-local_date")[:7],
    }, status=status)


@login_required
@never_cache
@require_POST
def verify_send(request):
    pref = Preference.objects.filter(user=request.user).first()
    if not settings.JOBFEEDS_MAIL_ENABLED or not pref or not pref.email or pref.verified:
        messages.info(request, "Save an unverified email and ensure mail is configured.")
        return redirect("jobfeeds:home")
    now = timezone.now()
    from django.db.models import Q
    nonce = token_hex(24)
    claimed = Preference.objects.filter(pk=pref.pk, email=pref.email, verified=False).filter(
        Q(verification_sent__isnull=True) | Q(verification_sent__lt=now - timedelta(minutes=10))
    ).update(verification_sent=now, nonce=nonce)
    if not claimed:
        messages.info(request, "Please wait ten minutes before requesting another verification.")
        return redirect("jobfeeds:home")
    token = signing.dumps({"user": request.user.pk, "email": pref.email, "nonce": nonce}, salt=SALT)
    link = request.build_absolute_uri(reverse("jobfeeds:verify", args=[token]))
    try:
        send_email(pref.email, "Verify your job digest email",
                   "Confirm your email while signed in:\n" + link + "\nThis link expires in one hour. Ignore if not requested.")
        messages.success(request, "Verification email sent.")
    except Exception:
        messages.error(request, "Verification could not be sent. Check mail configuration or try later.")
    return redirect("jobfeeds:home")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def verify(request, token):
    try:
        data = signing.loads(token, salt=SALT, max_age=3600)
    except signing.BadSignature:
        data = {}
    valid = data.get("user") == request.user.pk and Preference.objects.filter(
        user=request.user, email=data.get("email", ""), nonce=data.get("nonce", ""), verified=False
    ).exclude(nonce="").exists()
    if request.method == "POST" and valid:
        updated = Preference.objects.filter(user=request.user, email=data["email"],
                                             nonce=data["nonce"], verified=False).update(verified=True, nonce="")
        if updated:
            messages.success(request, "Email verified. Enable the digest in preferences.")
            return redirect("jobfeeds:home")
    return render(request, "jobfeeds/verify.html", {"valid": valid and request.method == "GET"},
                  status=200 if valid and request.method == "GET" else 400)
