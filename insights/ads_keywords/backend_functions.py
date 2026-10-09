# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Whitelisted entry points exposed as Insights Query v3 *backend functions*.

Each function accepts the query's ``backend_function_args`` (a JSON object) and
returns a list of plain dicts that Insights loads into a memtable for charting.

Example query args::

    {
      "resource": "search_term_view",
      "date_range": "LAST_30_DAYS",
      "limit": 500
    }

When ``keywords`` / ``keywords_text`` are omitted the live keywords are pulled
from the **Google Ads API** (configured in the Google Ads Settings form). Pass
``keywords`` explicitly to classify a pasted list/CSV instead.

Classification is entirely rule-based (see :mod:`insights.ads_keywords.engine`):
same input, same output, plus a written reason per row - which is what keeps an
ad-spend decision auditable. Keywords the rules cannot place come back as
``UNKNOWN``; :func:`keywords_for_review` lists them so a human can codify them
into a signal term instead of guessing.
"""

from __future__ import annotations

import frappe

from insights.ads_keywords import engine, google_ads, sources
from insights.ads_keywords.config import (
    INTENT_ACTIONS,
    POTENTIAL_NEGATIVE,
    UNKNOWN,
    KeywordIntentConfig,
)
from insights.decorators import insights_whitelist

# Presentation labels: a backend function returns ready-to-render rows, so the
# chart's column names come straight from these keys.
KEYWORD_LABELS = {
    "keyword": "Keyword",
    "intent": "Intent",
    "confidence": "Confidence",
    "suggested_action": "Suggested action",
    "reason": "Reason",
    "source": "Classified by",
    "search_volume": "Search volume",
    "impressions": "Impressions",
    "clicks": "Clicks",
    "cost": "Cost",
    "conversions": "Conversions",
    "match_type": "Match type",
    "accounts": "Accounts",
}

SUMMARY_LABELS = {
    "intent": "Intent",
    "keywords": "Keywords",
    "share_pct": "Share %",
    "suggested_action": "Suggested action",
}

def _merge_args(filters, kwargs) -> dict:
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    merged = dict(filters or {})
    merged.update(kwargs or {})
    return {key: value for key, value in merged.items() if value is not None}


def _config(merged: dict) -> KeywordIntentConfig:
    return KeywordIntentConfig.from_dict(merged)


def _records(merged: dict) -> list[dict]:
    """Explicit keywords if given, otherwise the live Google Ads data."""
    records = sources.parse_keywords(merged.get("keywords") or merged.get("keywords_text"))
    if records:
        return records
    return google_ads.fetch_keywords(
        resource=merged.get("resource"),
        customer_id=merged.get("customer_id"),
        date_range=merged.get("date_range"),
        limit=merged.get("limit"),
        min_impressions=merged.get("min_impressions"),
        expand_manager=merged.get("expand_manager"),
    )


def _classify(merged: dict) -> list[dict]:
    """Run the rule engine over the selected keywords."""
    return engine.classify_all(_records(merged), config=_config(merged))


def _rename(rows: list[dict], mapping: dict) -> list[dict]:
    return [{mapping.get(key, key): value for key, value in row.items()} for row in rows]


@insights_whitelist()
def classify_keywords(filters=None, **kwargs):
    """One row per keyword with its intent, confidence and suggested action."""
    rows = _classify(_merge_args(filters, kwargs))
    return _rename(rows, KEYWORD_LABELS)


@insights_whitelist()
def keyword_intent_summary(filters=None, **kwargs):
    """Counts per intent - the source for the intent breakdown bar chart."""
    rows = _classify(_merge_args(filters, kwargs))
    total = len(rows) or 1

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["intent"]] = counts.get(row["intent"], 0) + 1

    summary = [
        {
            "intent": intent,
            "keywords": count,
            "share_pct": round(count * 100 / total, 1),
            "suggested_action": INTENT_ACTIONS.get(intent, ""),
        }
        for intent, count in counts.items()
    ]
    summary.sort(key=lambda row: row["keywords"], reverse=True)
    return _rename(summary, SUMMARY_LABELS)


@insights_whitelist()
def negative_keyword_candidates(filters=None, **kwargs):
    """Only the keywords flagged as a potential negative keyword, in a table."""
    rows = _classify(_merge_args(filters, kwargs))
    negatives = [row for row in rows if row["intent"] == POTENTIAL_NEGATIVE]
    negatives.sort(key=lambda row: row.get("impressions") or row.get("search_volume") or 0, reverse=True)
    return _rename(negatives, KEYWORD_LABELS)


@insights_whitelist()
def keywords_for_review(filters=None, **kwargs):
    """The keywords the rules could not place, biggest spend first.

    This is the deterministic stand-in for "ask a model": instead of guessing on
    a keyword the policy does not cover, the term is listed here so someone can
    decide once and add it to a signal term list (``service_terms``,
    ``irrelevant_terms``, ...). The list converges to empty as the vocabulary is
    learned, and every decision stays reviewable.

    ``max_confidence`` widens the net: ``0`` (default) lists only ``UNKNOWN``,
    ``0.6`` also lists the low-confidence matches (a bare service term, or a
    product term next to a commercial signal).
    """
    merged = _merge_args(filters, kwargs)
    try:
        ceiling = float(merged.get("max_confidence") or 0)
    except (TypeError, ValueError):
        ceiling = 0.0

    rows = [
        row
        for row in _classify(merged)
        if row["intent"] == UNKNOWN or (ceiling and float(row.get("confidence") or 0) <= ceiling)
    ]
    rows.sort(key=lambda row: row.get("impressions") or row.get("search_volume") or 0, reverse=True)
    return _rename(rows, KEYWORD_LABELS)
