"""Read-only OAuth2 for Google and Microsoft: session state, PKCE and encrypted tokens."""
import base64
import hashlib
import json
import os
import re
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from django.urls import reverse
from django.utils import timezone

from .models import MailConnection


class MailError(Exception):
    pass


PROVIDERS = ("google", "microsoft")


def configured(provider):
    name = "GOOGLE" if provider == "google" else "MICROSOFT"
    return bool(os.environ.get(f"JOBWEB_{name}_CLIENT_ID") and os.environ.get(f"JOBWEB_{name}_CLIENT_SECRET"))


def provider_config(provider, request):
    if provider not in PROVIDERS:
        raise MailError("Unsupported mailbox provider.")
    name = "GOOGLE" if provider == "google" else "MICROSOFT"
    client_id = os.environ.get(f"JOBWEB_{name}_CLIENT_ID", "").strip()
    secret = os.environ.get(f"JOBWEB_{name}_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        raise MailError("Mailbox OAuth has not been configured by the administrator.")
    if provider == "google":
        authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
        token_url = "https://oauth2.googleapis.com/token"
        scope = "https://www.googleapis.com/auth/gmail.readonly"
    else:
        tenant = os.environ.get("JOBWEB_MICROSOFT_TENANT", "common")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", tenant):
            raise MailError("Invalid Microsoft tenant setting.")
        authorization_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        scope = "offline_access User.Read Mail.Read"
    redirect_uri = os.environ.get(f"JOBWEB_{name}_REDIRECT_URI") or request.build_absolute_uri(
        reverse("mailbox_callback", args=[provider]))
    if not redirect_uri.startswith("https://") and not (
        request.get_host().split(":")[0] in {"localhost", "127.0.0.1", "testserver"}
        and redirect_uri.startswith("http://")
    ):
        raise MailError("The OAuth redirect URI must use HTTPS.")
    return dict(client_id=client_id, client_secret=secret, authorization_url=authorization_url,
                token_url=token_url, redirect_uri=redirect_uri, scope=scope)


def start_authorization(request, provider):
    cfg = provider_config(provider, request)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    request.session[f"mail_oauth_{provider}"] = {
        "state": state, "verifier": verifier, "timestamp": timezone.now().timestamp(),
        "user_id": request.user.pk,
    }
    params = dict(client_id=cfg["client_id"], redirect_uri=cfg["redirect_uri"],
                  response_type="code", scope=cfg["scope"], state=state,
                  code_challenge=challenge, code_challenge_method="S256")
    if provider == "google":
        params.update(access_type="offline", prompt="consent")
    return cfg["authorization_url"] + "?" + urlencode(params)


def oauth_request(url, params):
    request = Request(url, urlencode(params).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
    }, method="POST")
    try:
        with urlopen(request, timeout=12) as response:
            data = json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise MailError("The provider rejected the mailbox authorization or refresh.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise MailError("The provider returned no access token.")
    return data


def _fernet():
    key = os.environ.get("JOBWEB_MAIL_TOKEN_KEY")
    if not key:
        raise MailError("Mailbox token encryption is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise MailError("Invalid mailbox encryption key.") from exc


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode("ascii")


def decrypt(value):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise MailError("Cannot decrypt mailbox credentials. Reconnect the account.") from exc


def complete_authorization(request, provider):
    cfg = provider_config(provider, request)
    saved = request.session.pop(f"mail_oauth_{provider}", None)
    state = request.GET.get("state", "")
    if (not saved or not state or not secrets.compare_digest(saved.get("state", ""), state)
            or saved.get("user_id") != request.user.pk
            or timezone.now().timestamp() - saved.get("timestamp", 0) > 600):
        raise MailError("OAuth state is invalid or expired. Connect again.")
    if request.GET.get("error"):
        raise MailError("Mailbox access was declined.")
    code = request.GET.get("code", "")
    if not code:
        raise MailError("Authorization code is missing.")
    data = oauth_request(cfg["token_url"], {
        "grant_type": "authorization_code", "code": code, "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"], "redirect_uri": cfg["redirect_uri"],
        "code_verifier": saved["verifier"],
    })
    refresh = data.get("refresh_token", "")
    existing = MailConnection.objects.filter(user=request.user, provider=provider).first()
    if not refresh and existing and existing.refresh_token_ciphertext:
        refresh = decrypt(existing.refresh_token_ciphertext)
    MailConnection.objects.update_or_create(user=request.user, provider=provider, defaults={
        "access_token_ciphertext": encrypt(data["access_token"]),
        "refresh_token_ciphertext": encrypt(refresh) if refresh else "",
        "expires_at": timezone.now() + timedelta(seconds=max(0, int(data.get("expires_in", 3600)))),
        "needs_reconnect": False,
    })


def access_token(connection, request):
    if connection.needs_reconnect:
        raise MailError("Mailbox needs reconnection.")
    if connection.expires_at and connection.expires_at > timezone.now() + timedelta(seconds=60):
        return decrypt(connection.access_token_ciphertext)
    if not connection.refresh_token_ciphertext:
        connection.needs_reconnect = True
        connection.save(update_fields=["needs_reconnect"])
        raise MailError("Mailbox authorization expired. Reconnect.")
    cfg = provider_config(connection.provider, request)
    try:
        data = oauth_request(cfg["token_url"], {
            "grant_type": "refresh_token", "refresh_token": decrypt(connection.refresh_token_ciphertext),
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        })
    except MailError:
        connection.needs_reconnect = True
        connection.save(update_fields=["needs_reconnect"])
        raise
    connection.access_token_ciphertext = encrypt(data["access_token"])
    if data.get("refresh_token"):
        connection.refresh_token_ciphertext = encrypt(data["refresh_token"])
    connection.expires_at = timezone.now() + timedelta(seconds=max(0, int(data.get("expires_in", 3600))))
    connection.save(update_fields=["access_token_ciphertext", "refresh_token_ciphertext", "expires_at"])
    return data["access_token"]


def provider_get(url, token, *, extra_headers=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=12) as response:
            return json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise MailError("Unable to retrieve mailbox messages. Reconnect if access was revoked.") from exc
