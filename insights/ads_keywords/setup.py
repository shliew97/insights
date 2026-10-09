# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Seeds the Google Ads keyword-analysis workbook and dashboard into Insights.

Run once (idempotent by title unless ``force``)::

    bench --site <site> execute insights.ads_keywords.setup.seed_google_ads_keyword_dashboard

Every chart is backed by an ``Insights Query v3`` of type *backend function*
that calls :mod:`insights.ads_keywords.backend_functions`, so
the whole dashboard runs on the classifier without duplicating it in SQL.
"""

from __future__ import annotations

import json

import frappe

from insights.insights.doctype.insights_workbook.insights_workbook import import_workbook

WORKBOOK_TITLE = "Google Ads Keyword Analysis"
BACKEND_MODULE = "insights.ads_keywords.backend_functions"

DEFAULT_FILTERS = {
    # live Google Ads data; account + credentials come from "Google Ads Settings"
    "resource": "search_term_view",
    "date_range": "LAST_30_DAYS",
    "limit": 500,
    "expand_manager": True,
}


def _now() -> str:
    """Timestamp that also works outside a frappe request/app context."""
    try:
        return str(frappe.utils.now())
    except Exception:
        from datetime import datetime

        return str(datetime.now())


def _dim(name: str, label: str | None = None, data_type: str = "String", **extra) -> dict:
    column = {
        "column_name": name,
        "data_type": data_type,
        "dimension_name": name,
        "label": label or name,
        "value": name,
    }
    column.update(extra)
    return column


def _measure(name: str, aggregation: str = "sum", data_type: str = "Decimal", label: str | None = None):
    label = label or f"{aggregation}_of_{name}"
    return {
        "aggregation": aggregation,
        "column_name": name,
        "data_type": data_type,
        "label": label,
        "measure_name": label,
        "value": label,
    }


def _table_config(rows: list[dict], order_by: list | None = None) -> dict:
    """Table chart config.

    A Table chart builds its data query from ``rows`` (dimensions) and ``values``
    (measures); passing every display column as a dimension with no measures
    returns the pre-computed backend-function rows unchanged.
    """
    return {
        "rows": rows,
        "columns": [],
        "values": [],
        "filters": {"filters": [], "logical_operator": "And"},
        "limit": 200,
        "order_by": order_by or [],
        "show_column_totals": False,
        "show_filter_row": True,
        "show_row_totals": False,
    }


def _share_with_organization(doctype: str, name: str) -> None:
    """Share an Insights resource read-only with every Insights user."""
    from insights.utils import DocShare

    share = DocShare.get_or_create_doc(share_doctype=doctype, share_name=name, everyone=1)
    share.read = 1
    share.notify_by_email = 0
    share.save(ignore_permissions=True)


def _query(query_id: str, title: str, function_name: str, filters: dict) -> dict:
    return {
        "name": query_id,
        "title": title,
        "workbook": "10",
        "use_live_connection": 0,
        "is_script_query": 0,
        "is_builder_query": 0,
        "is_native_query": 0,
        "is_backend_function": 1,
        "backend_function_path": f"{BACKEND_MODULE}.{function_name}",
        "backend_function_args": json.dumps(filters),
        "operations": [],
    }


def _bar_config(x_column: str, x_label: str, y_column: str, y_label: str) -> dict:
    return {
        "filters": {"filters": [], "logical_operator": "And"},
        "grouping": "grouped",
        "limit": 100,
        "normalize": False,
        "order_by": [{"column": {"column_name": x_column, "type": "column"}, "direction": "asc"}],
        "show_data_labels": True,
        "split_by": {"column_name": "", "data_type": "String", "dimension_name": ""},
        "stack": False,
        "swap_axes": False,
        "x_axis": {"dimension": _dim(x_column, x_label)},
        "y2_axis": None,
        "y2_axis_type": "line",
        "y_axis": {
            "normalize": False,
            "series": [{"measure": _measure(y_column, "sum", "Integer", y_label)}],
            "show_axis_label": True,
            "show_data_labels": True,
            "stack": False,
        },
    }


def get_workbook_definition(filters: dict | None = None) -> dict:
    """Return an importable workbook payload for the keyword dashboard."""
    filters = {**DEFAULT_FILTERS, **(filters or {})}

    queries = {
        "q_keywords": _query(
            "q_keywords", "Keywords - intent classification", "classify_keywords", filters
        ),
        "q_summary": _query("q_summary", "Intent summary", "keyword_intent_summary", filters),
        "q_negative": _query(
            "q_negative", "Negative keyword candidates", "negative_keyword_candidates", filters
        ),
        "q_review": _query(
            "q_review", "Needs review (rules could not place)", "keywords_for_review", filters
        ),
    }

    keyword_rows = [
        _dim("Keyword"),
        _dim("Intent"),
        _dim("Confidence", data_type="Decimal"),
        _dim("Suggested action"),
        _dim("Impressions", data_type="Integer"),
        _dim("Clicks", data_type="Integer"),
        _dim("Cost", data_type="Decimal"),
        _dim("Conversions", data_type="Decimal"),
        _dim("Accounts", data_type="Integer"),
        _dim("Reason"),
        _dim("Classified by"),
    ]
    order_by_intent = [{"column": {"column_name": "Intent", "type": "column"}, "direction": "asc"}]
    order_by_impressions = [
        {"column": {"column_name": "Impressions", "type": "column"}, "direction": "desc"}
    ]

    charts = {
        "chart_summary": {
            "name": "chart_summary",
            "title": "Keywords by intent",
            "workbook": "10",
            "query": "q_summary",
            "chart_type": "Bar",
            "config": _bar_config("Intent", "Intent", "Keywords", "Keywords"),
        },
        "chart_keywords": {
            "name": "chart_keywords",
            "title": "Keyword intent classification",
            "workbook": "10",
            "query": "q_keywords",
            "chart_type": "Table",
            "config": _table_config(keyword_rows, order_by_intent),
        },
        "chart_negative": {
            "name": "chart_negative",
            "title": "Negative keyword candidates",
            "workbook": "10",
            "query": "q_negative",
            "chart_type": "Table",
            "config": _table_config(keyword_rows, order_by_intent),
        },
        "chart_review": {
            "name": "chart_review",
            "title": "Needs review - turn these into signal terms",
            "workbook": "10",
            "query": "q_review",
            "chart_type": "Table",
            "config": _table_config(keyword_rows, order_by_impressions),
        },
    }

    dashboards = {
        "dash_ads_keywords": {
            "name": "dash_ads_keywords",
            "title": WORKBOOK_TITLE,
            "workbook": "10",
            "items": _dashboard_items(),
        }
    }

    return {
        "version": "1.0",
        "timestamp": _now(),
        "type": "Workbook",
        "name": "10",
        "doc": {"name": "10", "title": WORKBOOK_TITLE},
        "dependencies": {"queries": queries, "charts": charts, "dashboards": dashboards},
    }


def _dashboard_items() -> list[dict]:
    """Grid layout for the keyword dashboard (20 column grid)."""
    layout = [
        ("chart_summary", 0, 0, 20, 8),
        ("chart_keywords", 0, 8, 20, 13),
        ("chart_negative", 0, 21, 20, 10),
        ("chart_review", 0, 31, 20, 10),
    ]
    return [
        {
            "chart": chart,
            "type": "chart",
            "layout": {"i": f"item_{index}", "x": x, "y": y, "w": w, "h": h, "moved": False},
        }
        for index, (chart, x, y, w, h) in enumerate(layout)
    ]


def seed_google_ads_keyword_dashboard(filters: dict | None = None, force: bool = False):
    """Create the Google Ads keyword workbook/dashboard in Insights.

    Idempotent by workbook title unless ``force`` is set. The workbook and its
    dashboard are shared with the organization so every Insights user can open them.
    """
    existing = frappe.db.get_value("Insights Workbook", {"title": WORKBOOK_TITLE}, "name")

    if existing and force:
        frappe.delete_doc("Insights Workbook", existing, force=True, ignore_permissions=True)
        frappe.db.commit()
        existing = None

    if existing:
        _share_with_organization("Insights Workbook", existing)
        for dashboard in frappe.get_all("Insights Dashboard v3", {"workbook": existing}, pluck="name"):
            _share_with_organization("Insights Dashboard v3", dashboard)
        frappe.db.commit()
        return existing

    definition = get_workbook_definition(filters)
    workbook_name = import_workbook(frappe.parse_json(definition))
    _share_with_organization("Insights Workbook", workbook_name)
    for dashboard in frappe.get_all("Insights Dashboard v3", {"workbook": workbook_name}, pluck="name"):
        _share_with_organization("Insights Dashboard v3", dashboard)
    frappe.db.commit()
    return workbook_name
