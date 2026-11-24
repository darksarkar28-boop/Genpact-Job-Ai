"""Local practice rubric, not an assessment of hiring eligibility."""
import re


def _terms(value):
    return re.findall(r"\b[\w]+\b", value.casefold())


def _contains(text, term):
    return bool(re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text.casefold()))


def make_questions(context, profile):
    employer, role = context["employer"], context["role"]
    requirements = list(dict.fromkeys(s.strip() for s in context.get("interview_skills", "").split(",") if s.strip()))[:6]
    known = [s for s in profile.get("skills", []) if isinstance(s, str) and s.strip()]
    if not requirements:
        requirements = [s for s in known if _contains(context.get("posting_description", ""), s)][:6]
    requirements = requirements[:3]
    focus = ", ".join(requirements) or "a core responsibility in the supplied posting"
    return [
        {"text": f"Why are you interested in the {role} position at {employer}? Connect the posting to a confirmed example from your experience.",
         "points": ["because", "example", "result"], "type": "motivation"},
        {"text": f"For the {role} position at {employer}, walk through a technical approach involving {focus}. Explain trade-offs and how you would validate the result.",
         "points": requirements, "type": "technical"},
        {"text": f"Describe a relevant challenge for the {role} role at {employer}. Use situation, task, action and result; be clear about what you personally did.",
         "points": ["situation", "task", "action", "result"], "type": "behavioral"},
    ]


def review_answer(question, answer):
    words = _terms(answer)
    points = question["points"]
    present = [point for point in points if _contains(answer, point)]
    missing = [point for point in points if point not in present]
    if not words:
        return {"clarity": None, "technical_depth": None, "missing": points,
                "feedback": "No transcript available; no score assigned.", "excerpt": ""}
    # Observable cues only: never claim to measure factual accuracy or actual fluency.
    reasoning = any(_contains(answer, word) for word in ("because", "therefore", "trade-off", "tradeoff"))
    validation = any(_contains(answer, word) for word in ("test", "tests", "measure", "validate", "verify"))
    clarity = min(5, int(len(words) >= 12) + int(len(words) >= 30) +
                  int(reasoning) + int(len(set(words)) >= 15) + int(bool(present)))
    depth = None
    if question["type"] == "technical":
        depth = min(5, (round(3 * len(present) / len(points)) if points else 0) +
                    int(reasoning) + int(validation))
    hints = []
    if missing:
        hints.append("Explain these missing rubric points if relevant: " + ", ".join(missing) + ".")
    if not reasoning:
        hints.append("Explain why you chose your approach.")
    if question["type"] == "technical" and not validation:
        hints.append("Describe how you would test or verify the solution.")
    if len(words) < 30:
        hints.append("Add a concrete example and outcome rather than filler.")
    return {"clarity": clarity, "technical_depth": depth, "missing": missing,
            "feedback": " ".join(hints) or "Rubric cues present; review accuracy and specificity yourself.",
            "excerpt": answer[:240]}
