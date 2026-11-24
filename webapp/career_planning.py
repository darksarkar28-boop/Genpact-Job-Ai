"""Evidence-based career planning from candidate-supplied observations.

No external requests, invented market data, or candidate information in logs.
Salary observations are annual base pay, in the same explicit currency.
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

validate_url = URLValidator(schemes=["https", "http"])


def _text(row, key, maximum=200):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Each observation needs a valid {key}.")
    return value.strip()


def _source(row):
    url = _text(row, "source", 1000)
    try:
        validate_url(url)
    except ValidationError as exc:
        raise ValueError("Each observation needs an HTTP(S) source URL.") from exc
    return url


def _amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a positive annual base salary.") from exc
    if not amount.is_finite() or not Decimal("0.01") <= amount <= 10**12:
        raise ValueError("Enter a positive annual base salary no greater than 1 trillion.")
    return amount.quantize(Decimal("0.01"))


def _rows(rows):
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Supply a JSON array of at most 100 observation objects.")
    return rows


def compensation_band(rows, *, role, location, level, currency, since, today=None):
    today = today or datetime.date.today()
    matched = {}
    for row in _rows(rows):
        dimensions = [_text(row, key).casefold() for key in ("role", "location", "level", "currency")]
        source = _source(row)
        amount = _amount(row.get("annual_base"))
        try:
            date = datetime.date.fromisoformat(_text(row, "date"))
        except ValueError as exc:
            raise ValueError("Use YYYY-MM-DD observation dates.") from exc
        if date > today:
            raise ValueError("Salary observation dates cannot be in the future.")
        if dimensions == [v.strip().casefold() for v in (role, location, level, currency)] and since <= date:
            key = (source, date, amount)
            matched[key] = {"source": source, "date": date, "annual_base": amount}
    observations = sorted(matched.values(), key=lambda row: row["annual_base"])
    count = len(observations)
    sources = len({row["source"] for row in observations})
    result = {"count": count, "source_count": sources, "observations": observations,
              "band": None, "currency": currency.upper()}
    # Three unique observations and at least two source URLs before showing a range.
    if count >= 3 and sources >= 2:
        amounts = [row["annual_base"] for row in observations]
        median = (amounts[(count - 1)//2] + amounts[count//2]) / 2
        result["band"] = {"low": amounts[0], "median": median, "high": amounts[-1]}
    return result


def counter_offer(*, employer, role, currency, offer, target, band):
    offer, target = _amount(offer), _amount(target)
    if target <= offer:
        raise ValueError("A counter-offer target must exceed your actual offer.")
    evidence = ("Based on the supplied compensation observations, I would like to discuss"
                if band else "I would like to discuss")
    return (f"Thank you for the {role} offer at {employer}. I am excited about the opportunity. "
            f"{evidence} an annual base salary of {currency} {target:,.2f}, "
            f"compared with the current offer of {currency} {offer:,.2f}. "
            "Is there flexibility on base compensation or other components of the package? "
            "I would be happy to discuss the scope of the role and my relevant experience.")


def skill_gaps(rows, confirmed):
    """Rank exact, candidate-entered required skills by distinct posting coverage.

    This measures possible coverage gain, not causal hiring or salary impact.
    """
    known = {skill.strip().casefold() for skill in confirmed if isinstance(skill, str)}
    postings = {}
    for row in _rows(rows):
        source = _source(row)
        role = _text(row, "role")
        skills = row.get("required_skills")
        if not isinstance(skills, list) or not 1 <= len(skills) <= 40:
            raise ValueError("Each posting needs 1–40 required skills.")
        entry = postings.setdefault(source, {"role": role, "skills": {}})
        for skill in skills:
            value = _text({"skill": skill}, "skill", 100)
            entry["skills"].setdefault(value.casefold(), value)
    missing = {}
    for source, entry in postings.items():
        for key, label in entry["skills"].items():
            if key not in known:
                item = missing.setdefault(key, {"skill": label, "postings": []})
                item["postings"].append({"source": source, "role": entry["role"]})
    total = len(postings)
    ordered = sorted(missing.values(), key=lambda row: (-len(row["postings"]), row["skill"].casefold()))
    result = []
    for item in ordered[:3]:
        count = len(item["postings"])
        if count < 2:
            continue
        result.append({**item, "count": count, "total": total,
                       "action": f"Complete a practical exercise using {item['skill']}.",
                       "milestone": f"Publish one demonstrable {item['skill']} exercise with a passing test or review."})
    return result, total
