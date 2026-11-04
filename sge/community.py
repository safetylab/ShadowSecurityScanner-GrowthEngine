"""Judging whether a public thread is one we belong in.

Relevance says the thread is about our subject. Intent says whether
anyone asked for input. Both have to hold before a reply is worth
drafting, because an unprompted product mention is advertising even when
the product fits.
"""

import datetime as dt

from . import config, relevance

INTENT_ORDER = ("asking", "discussing", "mentioning")

# Weight by intent: someone asking what to use wants answers.
INTENT_BONUS = {"asking": 25, "discussing": 10, "mentioning": 0}


def load_rules():
    """Validated community.yml. Validation lives in sge.config so that
    `controller config-check` covers this file like every other one."""
    return config.load_community()


def detect_intent(text, settings):
    """Strongest intent signal present in the text."""
    lowered = (text or "").lower()
    signals = settings.get("intent_signals") or {}

    for name in INTENT_ORDER:
        for phrase in signals.get(name, []):
            if str(phrase).lower() in lowered:
                return name

    return "mentioning"


def should_avoid(text, settings):
    """Reasons to stay out regardless of relevance."""
    lowered = (text or "").lower()

    return [
        phrase for phrase in settings.get("avoid", [])
        if str(phrase).lower() in lowered
    ]


def age_days(timestamp, now=None):
    """Age of an ISO-8601 timestamp in days, or None if unparsable."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    text = (timestamp or "").strip()

    if not text:
        return None

    try:
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)

    return (moment - when).days


def evaluate_thread(thread, settings, keywords=None, now=None):
    """Score a thread and advise whether a human should reply.

    ``thread`` needs title, excerpt and created_at_source.

    The intent bonus only strengthens a thread that is already about our
    subject. Without that floor, "Pando - OpenAI's alternative to Git"
    scored 25 and was advised as a reply, on the strength of the word
    "alternative" alone: an unprompted plug in somebody else's thread.
    """
    rules = settings["rules"]
    text = f"{thread.get('title', '')} {thread.get('excerpt', '')}"

    verdict = relevance.evaluate(
        {"title": thread.get("title", ""),
         "summary": thread.get("excerpt", ""),
         "link": thread.get("url", "")},
        keywords,
    )

    intent = detect_intent(text, settings)
    topic_score = verdict["relevance"]
    score = min(100, topic_score + INTENT_BONUS.get(intent, 0))

    reasons = []
    advice = "skip"

    avoid = should_avoid(text, settings)
    age = age_days(thread.get("created_at_source"), now)
    max_age = rules.get("max_age_days", 120)
    threshold = rules.get("min_relevance", 25)
    topic_floor = rules.get("min_topic_relevance", threshold)

    if avoid:
        reasons.append(f"avoid list: {', '.join(avoid)}")
    elif verdict["excluded"]:
        reasons.append("off-topic keywords in the thread")
    elif topic_score < topic_floor:
        reasons.append(
            f"not about our subject (topic relevance {topic_score}, "
            f"needs {topic_floor})"
        )
    elif score < threshold:
        reasons.append(f"relevance {score} below {threshold}")
    elif age is not None and age > max_age:
        reasons.append(f"thread is {age} days old, over {max_age}")
    elif intent == "mentioning":
        reasons.append("nobody asked for input: replying would be a plug")
    elif intent == "asking":
        advice = "reply"
        reasons.append("someone is asking what to use")
    else:
        advice = "consider"
        reasons.append("relevant discussion, judge the room first")

    return {
        "relevance": score,
        "intent": intent,
        "advice": advice,
        "reasons": reasons,
        "matched": verdict["matched"],
    }
