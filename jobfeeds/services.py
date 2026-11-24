"""Persistent source snapshots and deterministic confirmed-profile matching."""
from datetime import timedelta
import json
import re
from html import unescape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags

from tools.job_feeds import Board, PUBLIC, PARTNER, ingest
from tools.partner_job_feeds import PartnerFeed, ingest_partner
from webapp.models import Profile
from .models import Posting, Source

MAX_STALE = timedelta(days=7)


def configured():
    config = json.loads(settings.JOBFEEDS_CONFIG)
    if not isinstance(config, dict):
        raise ValueError("feed configuration must be an object")
    boards = [Board(**item) for item in config.get("boards", [])]
    partners = [PartnerFeed(**item) for item in config.get("partners", [])]
    entries = [(f"{b.provider}:{b.slug}", b.provider, b.employer[:200], b, ingest) for b in boards]
    entries += [(f"{p.provider}:licensed", p.provider, "Authorized feed", p, ingest_partner) for p in partners]
    if len(entries) > 100 or len({e[0] for e in entries}) != len(entries):
        raise ValueError("duplicate or too many configured sources")
    return entries


def persist(source, started, result):
    # A newer refresh owns the source even if this response arrives late.
    # Acquiring a write lock before reading works on the supported SQLite DB.
    with transaction.atomic():
        owned = Source.objects.filter(pk=source.pk, last_attempt=started, enabled=True).update(
            status=result.status, invalid_count=result.invalid_count)
        if not owned:
            return False
        rows = {}
        for job in result.jobs:
            rows.setdefault(job.source_id, job)
            old = rows[job.source_id]
            if job.updated_at and (not old.updated_at or job.updated_at > old.updated_at):
                rows[job.source_id] = job
        for job in rows.values():
            Posting.objects.update_or_create(source=source, source_id=job.source_id, defaults={
                "employer": job.employer[:300], "title": job.title[:500],
                "location": job.location[:500], "url": job.url, "region": job.region,
                "description": strip_tags(unescape(job.description))[:20000],
                "legacy_key": job.legacy_key, "posted_at": job.posted_at, "updated_at": job.updated_at,
                "expires_at": job.expires_at, "last_seen": started, "active": not job.expired(started),
            })
        if result.status == "ok":
            Posting.objects.filter(source=source).exclude(last_seen=started).update(active=False)
            Source.objects.filter(pk=source.pk).update(last_success=started)
        return True


def refresh(*, public_reader=None, partner_reader=None):
    entries = configured()
    Source.objects.exclude(key__in=[e[0] for e in entries]).update(enabled=False)
    outcomes = []
    for key, provider, label, config, reader in entries:
        started = timezone.now()
        source, _ = Source.objects.get_or_create(key=key, defaults={"provider": provider, "label": label})
        claimed = Source.objects.filter(pk=source.pk).filter(
            Q(last_attempt__isnull=True) | Q(last_attempt__lt=started)
        ).update(last_attempt=started, enabled=True, label=label, status="refreshing")
        if not claimed:
            continue
        reader = (public_reader if reader is ingest else partner_reader) or reader
        try:
            result = reader(config)
            persist(source, started, result)
            outcomes.append((key, result.status))
        except (OSError, ValueError, TypeError):
            Source.objects.filter(pk=source.pk, last_attempt=started).update(status="unavailable")
            outcomes.append((key, "unavailable"))
    return outcomes


def provider_status():
    statuses = []
    for provider in PUBLIC + PARTNER:
        sources = list(Source.objects.filter(provider=provider, enabled=True).order_by("key"))
        statuses.append({"provider": provider, "sources": sources, "fallback":
                         "Public board not configured" if provider in PUBLIC else "Authorized feed required"})
    return statuses


def ranked(preference, now=None, limit=50):
    now = now or timezone.now()
    profile = Profile.objects.filter(user=preference.user, revision__gt=0).first()
    skills = [] if not profile else profile.data.get("skills", [])
    skills = list(dict.fromkeys(s.strip().casefold() for s in skills if isinstance(s, str) and s.strip()))[:100]
    query = Posting.objects.filter(active=True, source__enabled=True, last_seen__gte=now - MAX_STALE).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if preference.market != "all":
        query = query.filter(region=preference.market)
    if preference.role:
        query = query.filter(title__icontains=preference.role)
    if preference.location:
        query = query.filter(location__icontains=preference.location)
    results = []
    seen = set()
    for job in query.select_related("source").order_by("-last_seen", "pk").iterator():
        if job.url in seen:
            continue
        seen.add(job.url)
        haystack = (job.title + " " + job.description).casefold()
        matched = [s for s in skills if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", haystack)]
        # Match score is observed keyword coverage, not a hiring probability.
        score = round(100 * len(matched) / len(skills)) if skills else None
        results.append({"job": job, "score": score, "matched": matched, "denominator": len(skills)})
    results.sort(key=lambda row: (row["score"] if row["score"] is not None else -1,
                                  row["job"].posted_at or row["job"].last_seen, -row["job"].pk), reverse=True)
    return results[:limit]


def local_clock(zone, now):
    try:
        return now.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return None
