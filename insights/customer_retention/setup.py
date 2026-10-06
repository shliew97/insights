# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Seeds the Customer Retention workbook, charts and dashboard into Insights.

Run once (idempotent by title unless ``force``)::

    bench --site <site> execute insights.customer_retention.setup.seed_customer_retention_dashboard

Every chart is backed by an ``Insights Query v3`` of type *backend function*
that calls :mod:`insights.customer_retention.backend_functions`, so the whole
dashboard runs on the engine without duplicating the business logic in SQL.
"""

from __future__ import annotations

import json

import frappe

from insights.insights.doctype.insights_workbook.insights_workbook import import_workbook

WORKBOOK_TITLE = "Customer Retention"
BACKEND_MODULE = "insights.customer_retention.backend_functions"

DEFAULT_FILTERS = {
    # demo=False queries the real HealthLand POS data. Set demo=True to explore
    # the dashboard with deterministic synthetic data instead.
    "demo": False,
    # brand must match the value resolved by sources.resolve_brand
    # (outlets whose name contains "SOMA" -> "SOMA", everything else -> "HealthLand").
    "brand": "SOMA",
    "window": 90,
    "scope": "brand",
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

    A Table chart builds its data query from ``rows`` (dimensions) and
    ``values`` (measures); the backend ``summarize`` then groups by ``rows``.
    Passing every display column as a dimension with no measures returns the
    rows unchanged - exactly what a pre-computed backend-function table needs.
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


def _bar_config(x_label: str, y_label: str, order_column: str) -> dict:
    return {
        "filters": {"filters": [], "logical_operator": "And"},
        "grouping": "grouped",
        "limit": 100,
        "normalize": False,
        "order_by": [{"column": {"column_name": order_column, "type": "column"}, "direction": "asc"}],
        "show_data_labels": True,
        "split_by": {"column_name": "", "data_type": "String", "dimension_name": ""},
        "stack": False,
        "swap_axes": False,
        "x_axis": {"dimension": _dim("interval_bucket", x_label)},
        "y2_axis": None,
        "y2_axis_type": "line",
        "y_axis": {
            "normalize": False,
            "series": [{"measure": _measure("returning_customers", "sum", "Integer", y_label)}],
            "show_axis_label": True,
            "show_data_labels": True,
            "stack": False,
        },
    }


def _number_config(column: str, label: str, decimal: str = "1") -> dict:
    return {
        "comparison": False,
        "date_column": None,
        "decimal": decimal,
        "filters": {"filters": [], "logical_operator": "And"},
        "limit": 100,
        "negative_is_better": False,
        "number_column": [column],
        "number_column_options": [],
        "number_columns": [_measure(column, "sum", "Decimal", label)],
        "order_by": [],
        "shorten_numbers": False,
        "sparkline": False,
    }


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


def get_workbook_definition(filters: dict | None = None) -> dict:
    """Return an importable workbook payload for the retention dashboard."""
    filters = {**DEFAULT_FILTERS, **(filters or {})}

    queries = {
        "q_table_a": _query(
            "q_table_a", "Table A - Cumulative Second Visit Conversion", "second_visit_conversion", filters
        ),
        "q_table_b": _query(
            "q_table_b", "Table B - Monthly Cohort Retention", "monthly_cohort_retention", filters
        ),
        "q_outlet": _query(
            "q_outlet", "Table C - Originating Outlet Drill-down", "outlet_drill_down", filters
        ),
        "q_therapist": _query(
            "q_therapist", "Table C - Originating Therapist Drill-down", "therapist_drill_down", filters
        ),
        "q_interval": _query(
            "q_interval", "Return Interval Distribution", "return_interval_distribution", filters
        ),
        "q_headline": _query("q_headline", "Retention Headline", "retention_headline", filters),
        "q_quality": _query("q_quality", "Data Quality", "data_quality", filters),
        "q_summary": _query("q_summary", "Derived Customer Summary", "customer_summaries", filters),
    }

    table_a_rows = [
        _dim("First visit month"),
        _dim("Customers", data_type="Integer"),
        _dim("30 days"),
        _dim("60 days"),
        _dim("90 days"),
        _dim("180 days"),
    ]
    table_b_rows = [_dim("First visit month"), _dim("Customers", data_type="Integer")] + [
        _dim(f"Month {index}") for index in range(1, 7)
    ]
    table_c_rows = [
        _dim("Origin Group"),
        _dim("Eligible", data_type="Integer"),
        _dim("Brand return", data_type="Decimal"),
        _dim("Same provider return", data_type="Decimal"),
        _dim("2nd visit same provider", data_type="Integer"),
        _dim("2nd visit different provider", data_type="Integer"),
        _dim("2nd visit unknown/multiple", data_type="Integer"),
        _dim("No brand return", data_type="Integer"),
    ]
    quality_rows = [
        _dim("Indicator"),
        _dim("Value"),
        _dim("Note"),
    ]

    order_by_cohort = [
        {"column": {"column_name": "First visit month", "type": "column"}, "direction": "asc"}
    ]
    charts = {
        "chart_interval": {
            "name": "chart_interval",
            "title": "Time between first and second visit",
            "workbook": "10",
            "query": "q_interval",
            "chart_type": "Bar",
            "config": _bar_config("Interval", "Returning customers", "interval_bucket"),
        },
        "chart_median": {
            "name": "chart_median",
            "title": "Median days to second visit",
            "workbook": "10",
            "query": "q_headline",
            "chart_type": "Number",
            "config": _number_config("median_days_to_second_visit", "Median days"),
        },
        "chart_table_a": {
            "name": "chart_table_a",
            "title": "Table A - Cumulative second visit conversion",
            "workbook": "10",
            "query": "q_table_a",
            "chart_type": "Table",
            "config": _table_config(table_a_rows, order_by_cohort),
        },
        "chart_table_b": {
            "name": "chart_table_b",
            "title": "Table B - Monthly cohort retention",
            "workbook": "10",
            "query": "q_table_b",
            "chart_type": "Table",
            "config": _table_config(table_b_rows, order_by_cohort),
        },
        "chart_outlet": {
            "name": "chart_outlet",
            "title": "Table C - Originating outlet drill-down",
            "workbook": "10",
            "query": "q_outlet",
            "chart_type": "Table",
            "config": _table_config(table_c_rows),
        },
        "chart_therapist": {
            "name": "chart_therapist",
            "title": "Table C - Originating therapist drill-down",
            "workbook": "10",
            "query": "q_therapist",
            "chart_type": "Table",
            "config": _table_config(table_c_rows),
        },
        "chart_quality": {
            "name": "chart_quality",
            "title": "Data quality indicators",
            "workbook": "10",
            "query": "q_quality",
            "chart_type": "Table",
            "config": _table_config(quality_rows),

        },
    }

    dashboards = {
        "dash_retention": {
            "name": "dash_retention",
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
    """Grid layout for the retention dashboard (20 column grid)."""
    layout = [
        ("chart_median", 0, 0, 4, 3),
        ("chart_interval", 4, 0, 16, 8),
        ("chart_table_a", 0, 8, 20, 9),
        ("chart_table_b", 0, 17, 20, 9),
        ("chart_outlet", 0, 26, 10, 9),
        ("chart_therapist", 10, 26, 10, 9),
        ("chart_quality", 0, 35, 20, 10),
    ]
    return [
        {
            "chart": chart,
            "type": "chart",
            "layout": {"i": f"item_{index}", "x": x, "y": y, "w": w, "h": h, "moved": False},
        }
        for index, (chart, x, y, w, h) in enumerate(layout)
    ]


def _share_with_organization(doctype: str, name: str) -> None:
    """Share an Insights resource read-only with every Insights user."""
    from insights.utils import DocShare

    share = DocShare.get_or_create_doc(share_doctype=doctype, share_name=name, everyone=1)
    share.read = 1
    share.notify_by_email = 0
    share.save(ignore_permissions=True)


def seed_customer_retention_dashboard(filters: dict | None = None, force: bool = False):
    """Create the Customer Retention workbook/dashboard in Insights.

    Idempotent by workbook title unless ``force`` is set. The workbook and its
    dashboard are shared with the organization so that every Insights user (not
    just the creator) can open them.
    """
    existing = frappe.db.get_value("Insights Workbook", {"title": WORKBOOK_TITLE}, "name")

    if existing and force:
        # recreate from scratch so updated definitions (chart configs etc.) apply
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


INSIGHTS_USER_ROLE = "Insights User"
INSIGHTS_ADMIN_ROLE = "Insights Admin"
# Roles whose holders are the intended audience of the analytics dashboards.
INSIGHTS_AUDIENCE_ROLES = (
    "HAS Sales Summary Viewer",
    "HAS New Sales Summary Viewer",
    "HealthLand POS Manager",
    "Analytics",
)


def ensure_insights_access(users=None, roles=None, admin: bool = False) -> dict:
    """Grant the ``Insights User`` role so users can open Insights.

    Insights gates every API behind ``Insights User`` (or admin). Users without
    it get a blank SPA, so this helper makes the dashboard reachable.

    Users that are governed by a **Role Profile** cannot hold extra roles directly
    (frappe resets their roles from the profile on save), so the role is added to
    the profile and synced to its members instead. ``users`` are explicit logins;
    ``roles`` expand to all holders of those roles. Pass ``admin=True`` to also
    grant ``Insights Admin``.
    """
    roles = tuple(roles) if roles is not None else INSIGHTS_AUDIENCE_ROLES
    target = {user for user in (users or []) if user}
    for role in roles:
        target |= set(
            frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
        )
    target = {user for user in target if frappe.db.exists("User", user)}

    granted, promoted, profiles = [], [], set()
    for user in sorted(target):
        profile = frappe.db.get_value("User", user, "role_profile_name")
        if profile:
            profiles.add(profile)
            continue

        doc = frappe.get_doc("User", user)
        current = {row.role for row in doc.roles}
        missing_user = INSIGHTS_USER_ROLE not in current
        missing_admin = admin and INSIGHTS_ADMIN_ROLE not in current
        if not (missing_user or missing_admin):
            continue
        if missing_user:
            doc.append_roles(INSIGHTS_USER_ROLE)
            granted.append(user)
        if missing_admin:
            doc.append_roles(INSIGHTS_ADMIN_ROLE)
            promoted.append(user)
        doc.save(ignore_permissions=True)

    updated_profiles = []
    for profile in sorted(profiles):
        role_profile = frappe.get_doc("Role Profile", profile)
        current = {row.role for row in role_profile.roles}
        changed = False
        if INSIGHTS_USER_ROLE not in current:
            role_profile.append("roles", {"role": INSIGHTS_USER_ROLE})
            changed = True
        if admin and INSIGHTS_ADMIN_ROLE not in current:
            role_profile.append("roles", {"role": INSIGHTS_ADMIN_ROLE})
            changed = True
        if changed:
            role_profile.save(ignore_permissions=True)
            role_profile.update_all_users()
            updated_profiles.append(profile)

    frappe.db.commit()
    return {
        "target_users": sorted(target),
        "granted": granted,
        "profiles_updated": updated_profiles,
        "promoted_admin": promoted,
    }
