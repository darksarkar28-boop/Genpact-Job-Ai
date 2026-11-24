"""Operator-configured public research feed, never a candidate-controlled URL.

The operator must verify that the selected feed is licensed/public-only and that
each registry ID maps to the correct employer. No profile is sent to the feed.
"""
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import URLError

from tools.model_service import NoRedirect
from tools.shared_research_cache import ResearchCache

MAX_FEED_BYTES = 512 * 1024


class PublicFeedUnavailable(RuntimeError):
    pass


def configured_feed(environ=None, opener=None):
    environ = os.environ if environ is None else environ
    raw = environ.get("JOB_AI_PUBLIC_RESEARCH_FEEDS")
    if not raw:
        return None
    try:
        entries = json.loads(raw)
        if not isinstance(entries, dict):
            raise ValueError()
        for key, endpoint in entries.items():
            ResearchCache.validate_key(key)
            parts = urlsplit(endpoint)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise PublicFeedUnavailable("invalid public research feed configuration") from None
    transport = opener or build_opener(NoRedirect())

    def fetch(employer_id):
        endpoint = entries.get(employer_id)
        if endpoint is None:
            raise PublicFeedUnavailable("no approved public feed for employer")
        try:
            with transport.open(Request(endpoint, headers={"Accept": "application/json"}), timeout=8) as response:
                raw = response.read(MAX_FEED_BYTES + 1)
            if len(raw) > MAX_FEED_BYTES:
                raise ValueError()
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"employer_id", "sources"} or data["employer_id"] != employer_id:
                raise ValueError()
            return ResearchCache._validate(data["sources"])
        except (URLError, OSError, ValueError, TypeError):
            raise PublicFeedUnavailable("public research feed unavailable or invalid") from None

    return fetch
