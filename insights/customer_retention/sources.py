# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Source adapters that feed the retention engine.

The brief describes a *logical* dataset (``brand_id``, ``customer_id`` ...).
This module maps that logical dataset onto the real HealthLand POS doctypes and
returns normalized records the engine understands:

======================  ==================================================
Logical field           HealthLand POS source
======================  ==================================================
``visit_id``            ``Booking.name``
``brand_id``            derived from ``Booking.outlet`` (SOMA outlets = SOMA)
``outlet_id``           ``Booking.outlet``
``customer_id``         ``Booking.member`` (the service recipient)
``booking_controller_id`` ``Booking.booking_member`` (kept separate)
``therapist_id``        ``Booking.masseur``
``visit_datetime``      ``Booking.appointment_from`` (actual service date)
``visit_status``        ``Booking.status`` (only ``Completed`` qualifies)
======================  ==================================================

Only completed bookings are loaded, so cancellations, no-shows and purchases
without attendance (``Order`` rows) never enter the cohort calculation. Online
bookings result in a ``Booking`` as well, so a single visit is never counted
twice.
"""

from __future__ import annotations

import frappe

from insights.customer_retention.config import DEFAULT_TIMEZONE, RetentionConfig

# The status a booking must have to be considered a qualifying visit.
_QUALIFYING_STATUS = "Completed"

# Default brand resolution: the *brand* in this business is not the legal
# ``company``. Outlets whose name contains one of these markers belong to that
# brand; everything else falls back to the default brand. Override per query via
# ``brand_rules`` / ``brand_map``.
DEFAULT_BRAND_RULES: tuple[tuple[str, str], ...] = (("SOMA", "SOMA"), ("HEALTHLAND", "HealthLand"))
DEFAULT_BRAND = "HealthLand"


def _clean(filters) -> dict:
    filters = dict(filters or {})
    return {key: value for key, value in filters.items() if value not in (None, "")}


def resolve_brand(outlet, company, filters: dict) -> str:
    """Resolve the reporting brand for an outlet.

    Resolution order: explicit ``brand_map`` (outlet -> brand) then
    ``brand_rules`` (substring -> brand, case-insensitive) then ``brand_field``
    (a raw column such as ``company``) and finally the default brand.
    """
    outlet = str(outlet or "")
    brand_map = filters.get("brand_map") or {}
    if isinstance(brand_map, str):
        brand_map = frappe.parse_json(brand_map)
    if outlet in brand_map:
        return brand_map[outlet]

    if filters.get("brand_field"):
        return str(company or filters.get("default_brand") or DEFAULT_BRAND)

    rules = filters.get("brand_rules") or DEFAULT_BRAND_RULES
    if isinstance(rules, str):
        rules = frappe.parse_json(rules)
    upper = outlet.upper()
    for rule in rules:
        needle, brand = (rule if isinstance(rule, (list, tuple)) else (rule, rule))
        if str(needle).upper() in upper:
            return str(brand)
    return str(filters.get("default_brand") or DEFAULT_BRAND)


def build_config(filters) -> RetentionConfig:
    """Build a :class:`RetentionConfig` from dashboard filters."""
    filters = _clean(filters)
    return RetentionConfig.from_dict(
        {
            "windows": filters.get("windows"),
            "scopes": filters.get("scopes"),
            "timezone": filters.get("timezone") or DEFAULT_TIMEZONE,
            "small_sample_threshold": filters.get("small_sample_threshold"),
            "include_month_0": filters.get("include_month_0"),
            "require_confirmed_history": filters.get("require_confirmed_history"),
            "coverage": filters.get("coverage"),
        }
    )


def fetch_visit_records(filters=None) -> list[dict]:
    """Load normalized visit records from HealthLand POS."""
    filters = _clean(filters)
    from_date = filters.get("from_date") or "2000-01-01"
    to_date = filters.get("to_date") or "2999-12-31"

    conditions = [
        "b.member IS NOT NULL",
        "b.member != ''",
        "b.appointment_from IS NOT NULL",
        "b.appointment_from >= %(from_date)s",
        "b.appointment_from < %(to_date)s",
        "b.status = %(status)s",
    ]
    params = {"from_date": from_date, "to_date": to_date, "status": _QUALIFYING_STATUS}
    if filters.get("outlet"):
        conditions.append("b.outlet = %(outlet)s")
        params["outlet"] = filters["outlet"]
    if filters.get("therapist"):
        conditions.append("b.masseur = %(therapist)s")
        params["therapist"] = filters["therapist"]

    rows = frappe.db.sql(
        f"""
        SELECT
            b.name AS visit_id,
            o.company AS company,
            b.outlet AS outlet_id,
            b.member AS customer_id,
            b.booking_member AS booking_controller_id,
            b.masseur AS therapist_id,
            b.appointment_from AS visit_datetime,
            b.status AS visit_status,
            b.check_in_time
        FROM `tabBooking` b
        LEFT JOIN `tabOutlet` o ON o.name = b.outlet
        WHERE {" AND ".join(conditions)}
        ORDER BY b.member, b.appointment_from
        """,
        params,
        as_dict=True,
    )

    records = []
    for row in rows:
        record = dict(row)
        record["brand_id"] = resolve_brand(record.get("outlet_id"), record.get("company"), filters)
        records.append(record)
    return records


def get_data_complete_through(filters=None):
    """Latest reliable service date across the selected brand(s)."""
    filters = _clean(filters)
    params = {"status": _QUALIFYING_STATUS}
    conditions = ["status = %(status)s", "appointment_from IS NOT NULL"]
    if filters.get("to_date"):
        conditions.append("appointment_from < %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("outlet"):
        conditions.append("outlet = %(outlet)s")
        params["outlet"] = filters["outlet"]

    value = frappe.db.sql(
        f"SELECT MAX(appointment_from) FROM `tabBooking` WHERE {' AND '.join(conditions)}", params
    )
    return value[0][0] if value and value[0][0] else None


def get_reliable_history_start():
    """Earliest reliable service date observed in the source."""
    value = frappe.db.sql(
        "SELECT MIN(appointment_from) FROM `tabBooking` WHERE status = %(status)s",
        {"status": _QUALIFYING_STATUS},
    )
    return value[0][0] if value and value[0][0] else None


def resolve_identity(filters):
    """Return ``(identity_map, unresolved_ids)`` from dashboard filters."""
    filters = _clean(filters)
    identity_map = filters.get("identity_map") or {}
    if isinstance(identity_map, str):
        identity_map = frappe.parse_json(identity_map)
    unresolved = filters.get("unresolved_identities") or []
    if isinstance(unresolved, str):
        unresolved = frappe.parse_json(unresolved)
    return identity_map, set(unresolved or [])


def demo_visit_records(customers: int = 240, seed: int = 7) -> list[dict]:
    """Deterministic synthetic visits so the dashboard is explorable without data."""
    import random
    from datetime import date, timedelta

    rng = random.Random(seed)
    brands = [("HealthLand", ["Bangsar", "Mid Valley", "Cheras"]), ("SOMA", ["SOMA KLCC", "SOMA PJ"])]
    therapists = ["Aina", "Bella", "Chong", "Dewi", "Farah"]
    records: list[dict] = []
    start = date(2025, 1, 1)
    horizon = start + timedelta(days=365)

    for index in range(customers):
        brand, outlets = rng.choice(brands)
        customer = f"{brand[:3].upper()}-{index:04d}"
        first = start + timedelta(days=rng.randint(0, 300))
        outlet = rng.choice(outlets)
        therapist = rng.choice(therapists)
        records.append(
            {
                "customer_id": customer,
                "brand_id": brand,
                "outlet_id": outlet,
                "therapist_id": therapist,
                "visit_datetime": first.isoformat(),
                "visit_status": "Completed",
            }
        )
        if rng.random() < 0.45:
            delay = rng.choice([7, 12, 20, 27, 40, 55, 75, 100, 140])
            second = first + timedelta(days=delay)
            if second <= horizon:
                records.append(
                    {
                        "customer_id": customer,
                        "brand_id": brand,
                        "outlet_id": rng.choice(outlets) if rng.random() < 0.4 else outlet,
                        "therapist_id": rng.choice(therapists) if rng.random() < 0.4 else therapist,
                        "visit_datetime": second.isoformat(),
                        "visit_status": "Completed",
                    }
                )
    return records
