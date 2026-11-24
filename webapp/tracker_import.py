"""Explicit, account-scoped import of legacy job_search_tracker.csv.

Never read CLI files from the web server, never silently coerce unknown status,
and never modify a previously imported or manually edited application on replay.
"""
import csv
import hashlib
import io
from datetime import date
from urllib.parse import urlsplit

from django import forms
from django.db import transaction

from .models import Application, ApplicationStatus, ApplicationStatusEvent

MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ROWS = 500
REQUIRED_COLUMNS = {"company", "role", "status"}
LEGACY = {"no response": "no_response", "offer declined": "offer_declined"}


class TrackerImportForm(forms.Form):
    tracker_csv = forms.FileField(
        label="Import CLI tracker CSV",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv"}),
        help_text="Import your job_search_tracker.csv. Existing imported rows and manual changes are preserved.",
    )

    def clean_tracker_csv(self):
        upload = self.cleaned_data["tracker_csv"]
        if upload.size > MAX_IMPORT_BYTES:
            raise forms.ValidationError("Tracker CSV must be 1 MiB or smaller.")
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("Choose a .csv tracker export.")
        return upload


def parse_tracker(upload):
    raw = upload.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("Tracker CSV exceeds the 1 MiB limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Tracker CSV must use UTF-8 encoding.") from exc
    if "\x00" in text:
        raise ValueError("Tracker CSV contains invalid NUL characters.")
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fields = reader.fieldnames or []
        if not REQUIRED_COLUMNS.issubset(fields):
            raise ValueError("Tracker CSV needs company, role and status columns.")
        if len(fields) != len(set(fields)) or any(not name for name in fields):
            raise ValueError("Tracker CSV contains duplicate or empty column names.")
        entries = []
        for row_num, row in enumerate(reader, start=2):
            if len(entries) >= MAX_IMPORT_ROWS:
                raise ValueError(f"Tracker CSV exceeds the {MAX_IMPORT_ROWS}-row limit.")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Row {row_num}: malformed CSV column count.")
            if any(len(value) > 4096 for value in row.values()):
                raise ValueError(f"Row {row_num}: field exceeds the allowed length.")
            company, role = row["company"].strip(), row["role"].strip()
            if not company or not role or len(company) > 250 or len(role) > 250:
                raise ValueError(f"Row {row_num}: invalid company or role.")
            status_raw = row["status"].strip().lower()
            status = LEGACY.get(status_raw, status_raw)
            if status not in ApplicationStatus.values:
                raise ValueError(f"Row {row_num}: unknown tracker status {status_raw!r}.")
            date_text = (row.get("date") or "").strip()
            if date_text:
                try:
                    tracker_date = date.fromisoformat(date_text)
                except ValueError as exc:
                    raise ValueError(f"Row {row_num}: date must be YYYY-MM-DD.") from exc
            else:
                tracker_date = None
            source = (row.get("source") or "").strip()
            parsed = urlsplit(source)
            url = source if parsed.scheme in ("https", "http") and parsed.netloc and len(source) <= 2048 else ""
            # Stable CLI identity excludes status/notes, which change as applications progress.
            # CV/letter paths distinguish duplicate applications to one employer and role.
            identity = (
                company.casefold(), role.casefold(), date_text,
                (row.get("channel") or "").strip().casefold(),
                source.casefold(),
                (row.get("cv_file") or "").strip().casefold(),
                (row.get("cover_letter_file") or "").strip().casefold(),
            )
            key = hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()
            entries.append({
                "company": company, "role": role, "status": status,
                "tracker_date": tracker_date, "source_url": url, "import_key": key,
            })
    except csv.Error as exc:
        raise ValueError("Tracker CSV has invalid quoting or line structure.") from exc
    if not entries:
        raise ValueError("Tracker CSV has no application rows.")
    return entries


def import_tracker(user, upload):
    entries = parse_tracker(upload)  # Validate the entire file before any writes.
    created = skipped = 0
    with transaction.atomic():
        for row in entries:
            defaults = {k: v for k, v in row.items() if k != "import_key"}
            app, inserted = Application.objects.get_or_create(
                user=user, import_key=row["import_key"], defaults=defaults,
            )
            if inserted:
                ApplicationStatusEvent.objects.create(
                    application=app, before="", after=app.status, origin="tracker_import",
                )
                created += 1
            else:
                # Replay is read-only, even if the candidate manually changed the stage.
                skipped += 1
    return created, skipped
