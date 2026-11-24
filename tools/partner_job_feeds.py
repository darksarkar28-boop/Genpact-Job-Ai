"""Read-only, licensed feed relay contract, not undocumented vendor endpoints.

A deployment operator must supply a feed they have rights to consume for each
provider. The relay translates the authorized upstream schema into this v1
contract; credentials remain server-side. No candidate-supplied fetch URLs.
"""
from dataclasses import dataclass, field
import re
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from tools.job_feeds import PARTNER, Job, canonical_url, fetch_json, request_json, text, timestamp

REGIONS = {"linkedin": "global", "indeed": "global", "naukri": "india",
           "hirist": "india", "instahyre": "india", "internshala": "india"}


@dataclass(frozen=True)
class PartnerFeed:
    provider: str
    endpoint: str
    token: str = field(repr=False)

    def __post_init__(self):
        if self.provider not in PARTNER:
            raise ValueError("unknown partner provider")
        p = urlsplit(self.endpoint)
        if (p.scheme != "https" or not p.hostname or p.username is not None or
                p.password is not None or p.query or p.fragment or p.port not in (None, 443)
                or any(c.isspace() or c == "\\" for c in self.endpoint)):
            raise ValueError("partner relay must be a fixed HTTPS URL without query or credentials")
        if not self.token or not re.fullmatch(r"[\x21-\x7e]{1,4096}", self.token):
            raise ValueError("valid server-side relay token required")


def normalize_partner(feed, row):
    if not isinstance(row, dict):
        raise ValueError("invalid partner job")
    source_id = row.get("source_id")
    if (not isinstance(source_id, (str, int)) or isinstance(source_id, bool)
            or not str(source_id).strip() or len(str(source_id)) > 2048
            or not text(row.get("employer")) or not text(row.get("title"))):
        raise ValueError("source id, employer and title required")
    region = row.get("region", REGIONS[feed.provider])
    if region not in ("global", "india"):
        raise ValueError("invalid market")
    return Job(feed.provider, "licensed", str(source_id).strip(),
               text(row["employer"]), text(row["title"]), text(row.get("location")),
               canonical_url(row.get("application_url")), region,
               timestamp(row.get("posted_at")), timestamp(row.get("updated_at")),
               timestamp(row.get("expires_at")), text(row.get("description")))


@dataclass
class PartnerResult:
    jobs: list[Job] = field(default_factory=list)
    status: str = "ok"
    invalid_count: int = 0


def ingest_partner(feed, fetch=fetch_json, sleep=time.sleep, max_pages=20):
    """GET one fixed endpoint with opaque cursor; never follow provider URLs."""
    if not 1 <= max_pages <= 100:
        raise ValueError("invalid page bound")
    result = PartnerResult()
    cursor = None
    seen_cursors = set()
    for _ in range(max_pages):
        url = feed.endpoint + ("?" + urlencode({"cursor": cursor}) if cursor else "")
        try:
            data = request_json(url, lambda u: fetch(u, {"Authorization": "Bearer " + feed.token}), sleep)
            if not isinstance(data, dict) or data.get("version") != 1 or data.get("provider") != feed.provider:
                raise ValueError("wrong relay schema or provider")
            rows = data["jobs"]
            if not isinstance(rows, list) or len(rows) > 500:
                raise ValueError("invalid relay job list")
            for row in rows:
                try:
                    result.jobs.append(normalize_partner(feed, row))
                except (ValueError, TypeError):
                    result.invalid_count += 1
            cursor = data.get("next_cursor")
            if cursor is None or cursor == "":
                result.status = "partial" if result.invalid_count else "ok"
                return result
            if not isinstance(cursor, str) or len(cursor) > 1024 or cursor in seen_cursors:
                raise ValueError("invalid or repeated cursor")
            seen_cursors.add(cursor)
        except (OSError, ValueError, KeyError, TypeError):
            result.status = "partial" if result.jobs else "unavailable"
            return result
    result.status = "partial"
    return result
