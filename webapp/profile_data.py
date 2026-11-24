"""The confirmed fields an account can save and reuse across document imports."""
import re

SCALARS = ("full_name", "headline", "email", "phone", "location", "summary")
RECORD_KEYS = {"experience": ("title", "employer", "dates"), "education": ("school", "degree", "dates")}
MAX_ENTRIES = 50


def unique_strings(values):
    result = {}
    for value in values:
        value = value.strip()
        if value:
            result.setdefault(value.casefold(), value)
    return list(result.values())


def skill_list(text):
    return unique_strings(re.split(r"[,;\n•]+", text))


def merge_profiles(saved, incoming):
    """Fill gaps, preserve confirmed values, and append new records in source order."""
    data = {field: saved.get(field) or incoming.get(field, "") for field in SCALARS}
    data["skills"] = unique_strings([*saved.get("skills", []), *incoming.get("skills", [])])
    for section, keys in RECORD_KEYS.items():
        rows = {}
        for row in [*saved.get(section, []), *incoming.get(section, [])]:
            identity = tuple(row.get(k, "").strip().casefold() for k in keys)
            if not all(identity):
                identity += (row.get("details", "").strip().casefold(),)
            if identity not in rows:
                rows[identity] = row.copy()
            else:
                confirmed = rows[identity]
                for field, value in row.items():
                    if not confirmed.get(field):
                        confirmed[field] = value
        data[section] = list(rows.values())
    return data
