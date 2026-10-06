# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Whitelisted entry points exposed as Insights Query v3 *backend functions*.

Each function accepts the query's ``backend_function_args`` (a JSON object) and
may also be called directly with keyword arguments. They all return a list of
plain dicts, which is what a backend-function query loads into a memtable so the
results can be charted and drilled into from within Insights.

Example query args::

    {
      "brand": "HealthLand",
      "window": 90,
      "demo": true
    }

When ``demo`` is true the functions use deterministic synthetic data, so the
seeded dashboard is explorable before the source mapping is confirmed.
"""

from __future__ import annotations

import re

import frappe

from insights.customer_retention import engine, sources
from insights.decorators import insights_whitelist


def _merge_args(filters, kwargs) -> dict:
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    merged = dict(filters or {})
    merged.update(kwargs or {})
    return {key: value for key, value in merged.items() if value is not None}


# ---------------------------------------------------------------------------
# Presentation labels
#
# Results are rendered as-is by the chart (its column headers come from the
# result column names), so the backend functions return human readable labels
# instead of raw snake_case keys. The engine keeps its stable keys.
# ---------------------------------------------------------------------------

TABLE_A_LABELS = {
    "cohort_month": "First visit month",
    "customers": "Customers",
    "days_30": "30 days",
    "days_60": "60 days",
    "days_90": "90 days",
    "days_180": "180 days",
}

TABLE_C_LABELS = {
    "origin_group": "Origin Group",
    "eligible": "Eligible",
    "brand_return_pct": "Brand return",
    "same_provider_return_pct": "Same provider return",
    "second_visit_same_provider": "2nd visit same provider",
    "second_visit_different_provider": "2nd visit different provider",
    "second_visit_unknown_or_multiple": "2nd visit unknown/multiple",
    "no_brand_return": "No brand return",
}

QUALITY_LABELS = {"indicator": "Indicator", "value": "Value", "note": "Note"}

_MONTH_COLUMN_RE = re.compile(r"^month_(\d+)(_pct|_retained)?$")
_MONTH_SUFFIX = {"_pct": " pct", "_retained": " retained"}


def _rename(rows: list[dict], mapping: dict) -> list[dict]:
    return [{mapping.get(key, key): value for key, value in row.items()} for row in rows]


def _rename_cohort_columns(rows: list[dict]) -> list[dict]:
    renamed = []
    for row in rows:
        new_row = {}
        for key, value in row.items():
            match = _MONTH_COLUMN_RE.match(key)
            if match:
                suffix = _MONTH_SUFFIX.get(match.group(2), "")
                new_row[f"Month {match.group(1)}{suffix}"] = value
            elif key == "cohort_month":
                new_row["First visit month"] = value
            elif key == "customers":
                new_row["Customers"] = value
            else:
                new_row[key] = value
        renamed.append(new_row)
    return renamed


def _build_coverage(records, brands) -> dict:
    """Default coverage register derived from the observed data range."""
    coverage: dict[str, dict] = {}
    for brand in brands:
        dates = sorted(
            r["visit_datetime"] for r in records if str(r.get("brand_id")) == str(brand) and r.get("visit_datetime")
        )
        if dates:
            coverage[brand] = {"reliable_history_start_date": dates[0]}
    return coverage


def analyse_filters(filters=None, **kwargs) -> engine.RetentionResult:
    """Run the engine for a set of dashboard filters."""
    filters = _merge_args(filters, kwargs)

    config = sources.build_config(filters)
    identity_map, unresolved_ids = sources.resolve_identity(filters)

    if filters.get("demo"):
        records = sources.demo_visit_records(
            customers=int(filters.get("demo_customers") or 240),
            seed=int(filters.get("demo_seed") or 7),
        )
    else:
        records = sources.fetch_visit_records(filters)

    cutoff = filters.get("cutoff") or (
        sources.get_data_complete_through(filters) if not filters.get("demo") else None
    )

    if not config.coverage and filters.get("use_observed_history_start"):
        brands = {str(r.get("brand_id")) for r in records if r.get("brand_id")}
        config = config.with_overrides(coverage=_build_coverage(records, brands))

    return engine.analyse(
        records, config=config, cutoff=cutoff, identity_map=identity_map, unresolved_ids=unresolved_ids
    )


def _filters_from(result, filters) -> dict:
    months = filters.get("cohort_months")
    if isinstance(months, str):
        months = frappe.parse_json(months)
    return {
        "brand": filters.get("brand"),
        "cohort_months": months,
    }


@insights_whitelist()
def customer_summaries(filters=None, **kwargs):
    """Derived one-row-per-customer-per-brand summary."""
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return engine.customer_summary_rows(result, **scope)


@insights_whitelist()
def second_visit_conversion(filters=None, **kwargs):
    """Table A - cumulative second-visit conversion by cohort month."""
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return _rename(
        engine.second_visit_conversion(result, windows=merged.get("windows"), **scope), TABLE_A_LABELS
    )


@insights_whitelist()
def monthly_cohort_retention(filters=None, **kwargs):
    """Table B - monthly cohort retention by cohort month."""
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return _rename_cohort_columns(
        engine.monthly_cohort_retention(
            result,
            scope=merged.get("scope") or "brand",
            max_months=merged.get("max_months"),
            include_month_0=merged.get("include_month_0"),
            **scope,
        )
    )


def _drill_down(filters, kwargs, dimension):
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return _rename(
        engine.drill_down(result, dimension=dimension, window=merged.get("window"), **scope),
        TABLE_C_LABELS,
    )


@insights_whitelist()
def outlet_drill_down(filters=None, **kwargs):
    """Table C - originating outlet drill-down for the selected window."""
    return _drill_down(filters, kwargs, "outlet")


@insights_whitelist()
def therapist_drill_down(filters=None, **kwargs):
    """Table C - originating therapist drill-down for the selected window."""
    return _drill_down(filters, kwargs, "therapist")


@insights_whitelist()
def return_interval_distribution(filters=None, **kwargs):
    """First-to-second visit interval distribution (bar chart source)."""
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return engine.return_interval_distribution(result, **scope)


@insights_whitelist()
def retention_headline(filters=None, **kwargs):
    """Median return time and share returning within the full window."""
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return engine.retention_headline(result, windows=merged.get("windows"), **scope)


@insights_whitelist()
def data_quality(filters=None, **kwargs):
    """Data-quality indicators (cutoff, unresolved identities, immature windows)."""
    merged = _merge_args(filters, kwargs)
    result = analyse_filters(merged)
    scope = _filters_from(result, merged)
    return _rename(engine.data_quality(result, **scope), QUALITY_LABELS)
