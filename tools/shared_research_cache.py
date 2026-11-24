"""Shared public employer research cache; cached facts are leads, not verification.

Use an authoritative employer identifier (e.g. registry ID with jurisdiction).
Only public research ingestion code may populate this cache, never candidate data.
SQLite serializes refreshes across processes sharing the same database.
"""
import json
from contextlib import closing
from math import isfinite
import re
import sqlite3
import time
from urllib.parse import urlsplit


class ResearchCache:
    def __init__(self, path, ttl=30 * 86400, clock=time.time):
        if not isfinite(ttl) or ttl <= 0:
            raise ValueError("positive TTL required")
        self.path, self.ttl, self.clock = path, ttl, clock
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS public_research
                       (employer TEXT PRIMARY KEY, fetched REAL NOT NULL, sources TEXT NOT NULL)""")

    @staticmethod
    def _validate(sources):
        if not isinstance(sources, list) or not 1 <= len(sources) <= 30:
            raise ValueError("public sources required")
        clean = []
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"url", "summary"}:
                raise ValueError("only public URL and summary fields allowed")
            url, summary = source["url"], source["summary"]
            if not isinstance(url, str) or not isinstance(summary, str) or not summary.strip() or len(summary) > 12000 or len(url) > 2048:
                raise ValueError("invalid source")
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("public HTTPS source required")
            clean.append({"url": url, "summary": summary})
        return clean

    @staticmethod
    def validate_key(employer_id):
        # A registry namespace and identifier, never a display name.
        if not isinstance(employer_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,39}:[A-Z0-9][A-Z0-9._-]{0,127}", employer_id):
            raise ValueError("canonical employer ID required")

    def lookup(self, employer_id):
        """Read fresh public research without allowing candidate-supplied writes."""
        self.validate_key(employer_id)
        db = sqlite3.connect(self.path)
        try:
            row = db.execute("SELECT fetched, sources FROM public_research WHERE employer = ?",
                             (employer_id,)).fetchone()
        finally:
            db.close()
        if not row or not 0 <= self.clock() - row[0] < self.ttl:
            return None
        return {"fetched_at": row[0], "sources": json.loads(row[1]), "cache_hit": True,
                "requires_verification": True}

    def get_or_refresh(self, employer_id, fetch_public):
        """Return sources and timestamp. Caller must independently verify final claims.

        The callback must be bounded and return only public, sourced employer facts.
        No account identity or candidate context belongs in its arguments or result.
        A failed refresh rolls back and does not mark stale data fresh.
        """
        self.validate_key(employer_id)
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fetched, sources FROM public_research WHERE employer = ?",
                             (employer_id,)).fetchone()
            now = self.clock()
            if row and 0 <= now - row[0] < self.ttl:
                db.commit()
                return {"fetched_at": row[0], "sources": json.loads(row[1]), "cache_hit": True,
                        "requires_verification": True}
            sources = self._validate(fetch_public(employer_id))
            fetched = self.clock()
            db.execute("INSERT OR REPLACE INTO public_research VALUES (?, ?, ?)",
                       (employer_id, fetched, json.dumps(sources)))
            db.commit()
            return {"fetched_at": fetched, "sources": sources, "cache_hit": False,
                    "requires_verification": True}
        finally:
            db.close()
