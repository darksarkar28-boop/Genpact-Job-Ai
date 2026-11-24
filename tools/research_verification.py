"""Independent verification of cached public source excerpts.

Only an exact operator-approved source URL for the verified employer identity
may be fetched. Cached summaries and candidate inputs never control a request.
A matching literal excerpt is a narrow evidence check, not a truth guarantee.
"""
import json
import os
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import URLError

from tools.model_service import NoRedirect
from tools.shared_research_cache import ResearchCache

MAX_BYTES = 512 * 1024


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template", "noscript"):
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def normalized(text):
    return " ".join(text.casefold().split())


def verify_sources(employer_id, leads, environ=None, opener=None):
    ResearchCache.validate_key(employer_id)
    if not leads:
        return []
    environ = os.environ if environ is None else environ
    try:
        config = json.loads(environ.get("JOB_AI_PUBLIC_SOURCE_URLS", "{}"))
        allowed = config.get(employer_id, [])
        if not isinstance(allowed, list) or not all(isinstance(url, str) for url in allowed):
            allowed = []
    except (ValueError, TypeError, AttributeError):
        allowed = []
    transport = opener or build_opener(NoRedirect())
    results = []
    for source in leads["sources"]:
        url = source["url"]
        result = {"url": url, "summary": source["summary"], "status": "manual_verification_required"}
        if url in allowed:
            try:
                parts = urlsplit(url)
                if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
                    raise ValueError("invalid approved URL")
                with transport.open(Request(url, headers={"Accept": "text/html, text/plain"}), timeout=5) as response:
                    raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("source too large")
                parser = VisibleText()
                parser.feed(raw.decode("utf-8", errors="replace"))
                phrase = normalized(source["summary"])
                result["status"] = ("literal_excerpt_found" if len(phrase) >= 20 and phrase in normalized(" ".join(parser.parts))
                                    else "excerpt_not_confirmed")
            except (URLError, OSError, ValueError):
                result["status"] = "source_unavailable"
        results.append(result)
    return results
