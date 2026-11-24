"""Restricted read-only Gmail and Graph mailbox adapters. No binary attachments."""
import base64
from html.parser import HTMLParser
from datetime import datetime, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit

from .mail_oauth import MailError, access_token, provider_get


def _date(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000, tz=dt_timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if "T" in value else parsedate_to_datetime(value)
        return parsed.replace(tzinfo=dt_timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError, OverflowError, IndexError):
        return None


def _google_text(payload):
    """Read only text/plain, never html links or attachments."""
    if not isinstance(payload, dict):
        return ""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        data = payload["body"]["data"]
        if len(data) > 30000:
            return ""
        try:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")[:2500]
        except (ValueError, TypeError):
            return ""
    for part in payload.get("parts", []):
        result = _google_text(part)
        if result:
            return result
    return ""


def gmail_messages(token):
    root = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    collected = []
    page_token = None
    # Two bounded pages at most; subsequent manual syncs can inspect recent mail again.
    for _ in range(2):
        query = {"maxResults": 50, "q": "newer_than:90d -in:sent -in:drafts"}
        if page_token:
            query["pageToken"] = page_token
        page = provider_get(root + "?" + urlencode(query), token)
        for item in page.get("messages", []):
            msg_id = item.get("id", "")
            if not msg_id or len(msg_id) > 512:
                continue
            full = provider_get(root + "/" + msg_id + "?format=full", token)
            headers = {h.get("name", "").lower(): h.get("value", "") for h in full.get("payload", {}).get("headers", [])}
            thread = full.get("threadId", "")
            safe_thread = thread if thread.isalnum() else ""
            collected.append({
                "id": msg_id, "subject": headers.get("subject", "")[:300],
                "sender": headers.get("from", "")[:250],
                "body": _google_text(full.get("payload", {})) or full.get("snippet", "")[:1000],
                "received_at": _date(full.get("internalDate")) or _date(headers.get("date")),
                "link": ("https://mail.google.com/mail/u/0/#all/" + safe_thread) if safe_thread else "",
            })
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return collected


class _ReadableHTML(HTMLParser):
    """Turn untrusted HTML email into text for classification; never render it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skipped = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skipped += 1
        elif tag in ("p", "br", "div", "li") and not self.skipped:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skipped:
            self.skipped -= 1

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)


def _outlook_body(msg):
    body = msg.get("body") or {}
    if not isinstance(body, dict):
        body = {}
    content = body.get("content") or ""
    if not isinstance(content, str):
        content = ""
    if str(body.get("contentType", "")).lower() == "html" and content:
        parser = _ReadableHTML()
        parser.feed(content[:16000])
        content = " ".join(parser.parts)
    return (content or msg.get("bodyPreview") or "")[:2500]


def outlook_messages(token):
    root = "https://graph.microsoft.com/v1.0/me/messages"
    query = {
        "$select": "id,subject,from,receivedDateTime,body,bodyPreview,webLink",
        "$top": "50", "$orderby": "receivedDateTime desc",
    }
    url = root + "?" + urlencode(query)
    result = []
    # Only follow provider-generated Microsoft Graph next links, never arbitrary redirects.
    for _ in range(2):
        page = provider_get(url, token, extra_headers={
            "Prefer": 'outlook.body-content-type="text"',
        })
        for msg in page.get("value", []):
            msg_id = msg.get("id", "")
            if not msg_id or len(msg_id) > 512:
                continue
            sender = (msg.get("from") or {}).get("emailAddress") or {}
            link = msg.get("webLink", "")
            host = urlsplit(link).hostname if isinstance(link, str) else None
            if not (link.startswith("https://") and host in {
                "outlook.office.com", "outlook.office365.com", "outlook.live.com",
            }):
                link = ""
            result.append({
                "id": msg_id, "subject": (msg.get("subject") or "")[:300],
                "sender": (sender.get("name", "") + " " + sender.get("address", ""))[:250],
                "body": _outlook_body(msg),
                "received_at": _date(msg.get("receivedDateTime")),
                "link": link,
            })
        next_url = page.get("@odata.nextLink", "")
        if not isinstance(next_url, str) or not next_url.startswith(root + "?"):
            break
        url = next_url
    return result


def load_messages(connection, request):
    token = access_token(connection, request)
    if connection.provider == "google":
        return gmail_messages(token)
    if connection.provider == "microsoft":
        return outlook_messages(token)
    raise MailError("Unknown provider.")
