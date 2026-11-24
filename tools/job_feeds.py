"""Public ATS job-feed ingestion foundation for issue #4.

Only configured employer boards are queried; other providers require an
authorized integration. URLs from postings are links, never fetch targets.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, build_opener, Request

from tools.job_key import make_key


PUBLIC = ("greenhouse", "lever", "ashby")
PARTNER = ("linkedin", "indeed", "naukri", "hirist", "instahyre", "internshala")
TRACKING = {"gh_src", "lever-source", "source", "ref", "trk", "fbclid", "gclid"}
BOARD = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")
MAX_BODY = 8 * 1024 * 1024


def access_status(provider):
    if provider in PUBLIC:
        return "public_board_configuration_required"
    if provider in PARTNER:
        return "authorized_integration_required"
    raise ValueError("unknown provider")


def text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def timestamp(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        if isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    raise ValueError("invalid job timestamp")


def canonical_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("invalid application URL")
    url = urlsplit(value.strip())
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("application URL must be HTTPS")
    if url.port not in (None, 443):
        raise ValueError("invalid application URL port")
    query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return urlunsplit(("https", url.hostname.lower(), url.path or "/", urlencode(query), ""))


@dataclass(frozen=True)
class Board:
    provider: str
    slug: str
    employer: str
    region: str = "global"

    def __post_init__(self):
        if self.provider not in PUBLIC or not BOARD.fullmatch(self.slug):
            raise ValueError("unsupported provider or invalid employer board")
        if not text(self.employer) or self.region not in ("global", "india"):
            raise ValueError("employer and valid region required")


@dataclass(frozen=True)
class Job:
    provider: str
    board: str
    source_id: str
    employer: str
    title: str
    location: str
    url: str
    region: str
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    description: str = ""

    @property
    def identity(self):
        return self.provider, self.board, self.source_id

    @property
    def legacy_key(self):
        # Reuse the CLI key for interoperability, not unique identity:
        # different openings may share an employer/title.
        return make_key(self.employer, self.title, self.url)

    def expired(self, now):
        return self.expires_at is not None and self.expires_at <= now


def normalize(board, row):
    if not isinstance(row, dict):
        raise ValueError("job must be an object")
    provider = board.provider
    if provider == "greenhouse":
        source_id = row.get("id")
        title = row.get("title")
        location = row.get("location") or {}
        location = location.get("name") if isinstance(location, dict) else ""
        url = row.get("absolute_url")
        posted, updated = row.get("first_published"), row.get("updated_at")
        description = row.get("content")
    elif provider == "lever":
        source_id = row.get("id")
        title = row.get("text")
        categories = row.get("categories") or {}
        location = categories.get("location") if isinstance(categories, dict) else ""
        url = row.get("applyUrl") or row.get("hostedUrl")
        posted, updated = row.get("createdAt"), row.get("updatedAt")
        description = row.get("descriptionPlain")
    else:
        source_id = row.get("id")
        url = row.get("applyUrl") or row.get("jobUrl")
        # Ashby's public board response does not always include an id.
        if not source_id and isinstance(row.get("jobUrl"), str):
            source_id = canonical_url(row["jobUrl"])
        title, location = row.get("title"), row.get("location")
        posted, updated = row.get("publishedAt"), None
        description = row.get("descriptionPlain")
    if (not isinstance(source_id, (str, int)) or isinstance(source_id, bool)
            or not str(source_id).strip() or not text(title)):
        raise ValueError("source id and title required")
    return Job(provider, board.slug, str(source_id).strip(), text(board.employer),
               text(title), text(location), canonical_url(url), board.region,
               timestamp(posted), timestamp(updated), timestamp(row.get("expiresAt")),
               description if isinstance(description, str) else "")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url):
    """Bounded HTTPS read; redirects are not followed."""
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "GenpactJobAI/1"})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("feed response exceeds size limit")
    return json.loads(raw)


def _retry_after(value):
    try:
        return min(60, max(0, int(value)))
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            return min(60, max(0, seconds))
        except (ValueError, TypeError, OverflowError):
            return 1


def request_json(url, fetch, sleep):
    for attempt in range(3):
        try:
            return fetch(url)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            sleep(_retry_after(error.headers.get("Retry-After") if error.headers else None))


@dataclass
class BoardResult:
    board: Board
    jobs: list[Job] = field(default_factory=list)
    status: str = "ok"
    invalid_count: int = 0


def ingest(board, fetch=fetch_json, sleep=time.sleep, max_pages=20):
    """Fetch a configured public board, with explicit partial/failure status."""
    if not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")
    result = BoardResult(board)
    slug = board.slug
    for page in range(max_pages):
        if board.provider == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        elif board.provider == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=100&skip={page * 100}"
        else:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            data = request_json(url, fetch, sleep)
            rows = data if board.provider == "lever" else data["jobs"]
            if not isinstance(rows, list):
                raise ValueError("invalid job list")
        except (OSError, ValueError, KeyError, TypeError):
            result.status = "partial" if result.jobs else "unavailable"
            return result
        for row in rows:
            if isinstance(row, dict) and row.get("isListed") is False:
                continue
            try:
                result.jobs.append(normalize(board, row))
            except (ValueError, TypeError):
                result.invalid_count += 1
        if board.provider != "lever" or len(rows) < 100:
            if result.invalid_count:
                result.status = "partial"
            return result
    result.status = "partial"  # pagination bound reached; do not claim full coverage
    return result


def collect(boards, fetch=fetch_json, sleep=time.sleep, now=None):
    """Independent failures and dedup by provider identity or canonical URL."""
    now = now or datetime.now(timezone.utc)
    results = [ingest(board, fetch, sleep) for board in boards]
    jobs = {}
    urls = set()
    for result in results:
        # Prefer the freshest copy of a repeated source identity within a board.
        for job in sorted(result.jobs, key=lambda j: j.updated_at or j.posted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
            if job.expired(now) or job.identity in jobs or job.url in urls:
                continue
            jobs[job.identity] = job
            urls.add(job.url)
    return list(jobs.values()), results
