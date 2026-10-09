# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Pure, rule-based keyword intent classifier.

Turns a Google Ads keyword into one of the intents defined in
:mod:`insights.ads_keywords.config` together with a
confidence, a human-readable reason and a suggested action.

Deliberately imports nothing from ``frappe`` so the rules can be unit-tested in
isolation.

There is no model in this path on purpose: the intent of a keyword is a *policy*
decision (what this business sells and refuses to bid on), not a language task.
A model cannot see the owner's negatives, conversions or margins, gives a
different answer every time the model changes, and its mistakes here cost real
money - see ``README`` ("Why no LLM"). Keywords the rules cannot place end up as
``UNKNOWN`` and are surfaced for a human to turn into a signal term.

The decision order (first match wins) is:

1. off-brand / policy-risky term          -> ``POTENTIAL_NEGATIVE``
2. intent-mismatch term (course/job...)   -> ``IRRELEVANT``
3. product term without a booking action  -> ``POTENTIAL_NEGATIVE``
4. service + booking action               -> ``HIGH_INTENT``
5. service + commercial signal            -> ``HIGH_INTENT``
6. service term alone                     -> ``HIGH_INTENT`` (low confidence)
7. anything else                          -> ``UNKNOWN`` (manual review)
"""

from __future__ import annotations

import re

from insights.ads_keywords.config import (
    HIGH_INTENT,
    INTENT_ACTIONS,
    IRRELEVANT,
    POTENTIAL_NEGATIVE,
    UNKNOWN,
    KeywordIntentConfig,
)

__all__ = [
    "normalize",
    "build_matchers",
    "classify_keyword",
    "classify_all",
]

# The config attributes that hold signal term lists.
SIGNAL_FIELDS: tuple[str, ...] = (
    "service_terms",
    "booking_terms",
    "high_intent_terms",
    "irrelevant_terms",
    "product_terms",
    "off_brand_terms",
)


def normalize(text) -> str:
    """Lower-case and collapse whitespace so matching is stable."""
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _pattern(term: str):
    """Compile a matcher for one term.

    Multi-word / non-alphanumeric phrases use plain substring matching; single
    tokens use word boundaries so e.g. ``spa`` does not match ``spain``.
    """
    term = term.lower()
    if " " in term or not term.isalnum():
        return re.compile(re.escape(term))
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def _compile(terms) -> list:
    return [(term, _pattern(term)) for term in terms if term]


def build_matchers(config: KeywordIntentConfig) -> dict[str, list]:
    """Pre-compile every signal list once per config."""
    return {name: _compile(getattr(config, name)) for name in SIGNAL_FIELDS}


def _matched(text: str, compiled: list) -> list[str]:
    return [term for term, pattern in compiled if pattern.search(text)]


def _join(terms) -> str:
    return ", ".join(f"'{term}'" for term in terms)


def classify_keyword(
    keyword,
    config: KeywordIntentConfig | None = None,
    matchers: dict | None = None,
) -> dict:
    """Classify a single keyword into an intent row."""
    config = config or KeywordIntentConfig()
    matchers = matchers or build_matchers(config)
    text = normalize(keyword)

    service = _matched(text, matchers["service_terms"])
    booking = _matched(text, matchers["booking_terms"])
    commercial = _matched(text, matchers["high_intent_terms"])
    irrelevant = _matched(text, matchers["irrelevant_terms"])
    product = _matched(text, matchers["product_terms"])
    off_brand = _matched(text, matchers["off_brand_terms"])

    intent = UNKNOWN
    confidence = 0.0
    reasons: list[str] = []

    if off_brand:
        intent = POTENTIAL_NEGATIVE
        confidence = 0.95
        reasons.append(f"off-brand/restricted term(s): {_join(off_brand)}")
    elif irrelevant:
        intent = IRRELEVANT
        confidence = 0.85
        reasons.append(f"intent-mismatch term(s): {_join(irrelevant)}")
    elif product and not (service and booking):
        intent = POTENTIAL_NEGATIVE
        # A product term next to a commercial signal is ambiguous -> lower confidence.
        confidence = 0.6 if commercial else 0.8
        reasons.append(f"product/equipment term(s): {_join(product)}")
        if commercial:
            reasons.append(f"but also commercial signal(s): {_join(commercial)}")
    elif service and booking:
        intent = HIGH_INTENT
        confidence = 0.9
        reasons.append(f"service term(s): {_join(service)}")
        reasons.append(f"booking action(s): {_join(booking)}")
    elif service and commercial:
        intent = HIGH_INTENT
        confidence = 0.8
        reasons.append(f"service term(s): {_join(service)}")
        reasons.append(f"commercial signal(s): {_join(commercial)}")
    elif service:
        intent = HIGH_INTENT
        confidence = 0.6
        reasons.append(f"service term(s): {_join(service)}")
    else:
        intent = UNKNOWN
        confidence = 0.0
        reasons.append("no recognised service or modifier terms")

    return {
        "keyword": str(keyword).strip(),
        "intent": intent,
        "confidence": round(confidence, 2),
        "reason": "; ".join(reasons),
        "suggested_action": INTENT_ACTIONS[intent],
        "source": "rule",
    }


def classify_all(records, config: KeywordIntentConfig | None = None) -> list[dict]:
    """Classify keyword records (dicts or plain strings).

    Extra fields present on a record (``search_volume``, ``cost``, ``clicks``,
    ``conversions``) are carried through onto the result row.
    """
    config = config or KeywordIntentConfig()
    matchers = build_matchers(config)
    rows: list[dict] = []
    for record in records or []:
        if isinstance(record, dict):
            keyword = record.get("keyword") or record.get("term") or record.get("query")
        else:
            keyword = record
        if keyword in (None, ""):
            continue
        row = classify_keyword(keyword, config=config, matchers=matchers)
        if isinstance(record, dict):
            for extra in ("search_volume", "impressions", "cost", "clicks", "conversions"):
                if record.get(extra) is not None:
                    row[extra] = record[extra]
        rows.append(row)
    return rows
