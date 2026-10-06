# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Pure cohort-retention engine.

Implements the measurement definitions from the developer brief:

* A *qualified visit* is a completed service visit, normalised to a single
  *visit-day* per customer per local calendar date.
* A *cohort* is the group of customers sharing the same first-visit month.
* *Return visits* happen on a later local calendar date within the brand.
* Cumulative windows count a return on days ``1..W`` inclusive (elapsed local
  calendar days from the first visit-date).
* HealthLand and SOMA (or any two brands) keep independent first-visit dates
  and cohorts - the engine groups everything by ``brand_id``.

This module deliberately imports nothing from ``frappe`` so the business logic
can be unit-tested in isolation.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from insights.customer_retention.config import MULTIPLE, UNKNOWN, RetentionConfig

__all__ = [
    "VisitDay",
    "CustomerSummary",
    "RetentionResult",
    "analyse",
    "build_visit_days",
    "build_customer_summaries",
    "second_visit_conversion",
    "monthly_cohort_retention",
    "drill_down",
    "return_interval_distribution",
    "retention_headline",
    "data_quality",
    "customer_summary_rows",
]


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_RECIPIENT_KEYS = ("customer_id", "recipient_customer_id", "recipient", "member", "customer")
_BRAND_KEYS = ("brand_id", "brand", "company")
_OUTLET_KEYS = ("outlet_id", "outlet", "branch_id", "location_id")
_THERAPIST_KEYS = ("therapist_id", "therapist", "masseur", "staff_id", "employee")
_DATE_KEYS = (
    "visit_datetime",
    "visit_date",
    "service_completed_at",
    "service_started_at",
    "appointment_from",
    "date",
)
_STATUS_KEYS = ("visit_status", "status")

_VERIFIED_IDENTITIES = frozenset({"", "verified", "confirmed", "resolved"})


def _pick(row: dict, keys: tuple[str, ...]):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_local_date(value, tz_name: str) -> date | None:
    """Coerce ``value`` (date/datetime/str) to a local calendar date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            try:
                value = value.astimezone(ZoneInfo(tz_name))
            except Exception:
                pass
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text[:20], fmt).date()
        except ValueError:
            continue
    return None


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _month_add(month: str, n: int) -> str:
    year, mon = (int(part) for part in month.split("-"))
    total = year * 12 + (mon - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _month_start(month: str) -> date:
    year, mon = (int(part) for part in month.split("-"))
    return date(year, mon, 1)


def _month_end(month: str) -> date:
    return _month_start(_month_add(month, 1)) - timedelta(days=1)


def _pct(count, denom):
    if not denom:
        return None
    return round(100 * count / denom, 1)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VisitDay:
    """A single qualifying visit-day for one customer within one brand."""

    brand_id: str
    customer_id: str
    visit_date: date
    outlets: tuple[str, ...]
    therapists: tuple[str, ...]
    sessions: int = 1
    unresolved_identity: bool = False

    @property
    def outlet_label(self) -> str:
        return _provider_label(self.outlets, UNKNOWN, MULTIPLE)

    @property
    def therapist_label(self) -> str:
        return _provider_label(self.therapists, UNKNOWN, MULTIPLE)


def _provider_label(values, unknown_label: str, multiple_label: str) -> str:
    values = sorted({value for value in values if value})
    if not values:
        return unknown_label
    if len(values) == 1:
        return values[0]
    return multiple_label


@dataclass(slots=True)
class CustomerSummary:
    """One derived summary row per customer per brand (brief: derived summary)."""

    brand_id: str
    customer_id: str
    cohort_month: str
    first_visit_date: date
    originating_outlet: str
    originating_therapist: str
    last_visit_date: date
    total_visit_days: int
    second_visit_date: date | None = None
    days_to_second_visit: int | None = None
    destination_outlet: str | None = None
    destination_therapist: str | None = None
    first_return_to_originating_outlet: date | None = None
    first_return_to_originating_therapist: date | None = None
    first_visit_history_confidence: str = "unknown"
    available_followup_days: int | None = None
    unresolved_identity: bool = False

    @property
    def brand_retained(self) -> bool:
        return self.second_visit_date is not None

    @property
    def days_to_return_originating_outlet(self) -> int | None:
        if self.first_return_to_originating_outlet is None:
            return None
        return (self.first_return_to_originating_outlet - self.first_visit_date).days

    @property
    def days_to_return_originating_therapist(self) -> int | None:
        if self.first_return_to_originating_therapist is None:
            return None
        return (self.first_return_to_originating_therapist - self.first_visit_date).days


@dataclass(slots=True)
class RetentionResult:
    config: RetentionConfig
    cutoff: date | None
    visit_days: list[VisitDay] = field(default_factory=list)
    summaries: list[CustomerSummary] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)

    @property
    def cohort_months(self) -> list[str]:
        return sorted({summary.cohort_month for summary in self.summaries})

    def visit_days_by_customer(self) -> dict[tuple[str, str], list[VisitDay]]:
        grouped: dict[tuple[str, str], list[VisitDay]] = defaultdict(list)
        for day in self.visit_days:
            grouped[(day.brand_id, day.customer_id)].append(day)
        for days in grouped.values():
            days.sort(key=lambda d: d.visit_date)
        return grouped


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def _normalize_row(row: dict, config: RetentionConfig, identity_map: dict, unresolved_ids: set[str]):
    customer = _pick(row, _RECIPIENT_KEYS)
    if customer is None:
        return None
    customer = str(customer)
    if identity_map:
        customer = str(identity_map.get(customer, customer))

    status = _pick(row, _STATUS_KEYS)
    status_norm = str(status).strip().lower() if status is not None else None

    identity_quality = str(row.get("identity_quality_status") or "").strip().lower()
    unresolved = customer in unresolved_ids or identity_quality not in _VERIFIED_IDENTITIES

    return {
        "brand": _pick(row, _BRAND_KEYS),
        "customer": customer,
        "outlet": _pick(row, _OUTLET_KEYS),
        "therapist": _pick(row, _THERAPIST_KEYS),
        "visit_date": _as_local_date(_pick(row, _DATE_KEYS), config.timezone),
        "status_norm": status_norm,
        "unresolved": unresolved,
    }


def _is_qualifying(normalized: dict, config: RetentionConfig) -> bool:
    status = normalized["status_norm"]
    if status is None:
        # No status information available - we cannot exclude it, so keep it.
        return True
    if status in config.excluded_statuses:
        return False
    return status in config.completed_statuses


def build_visit_days(
    records,
    config: RetentionConfig,
    identity_map: dict | None = None,
    unresolved_ids: set[str] | None = None,
    cutoff: date | None = None,
) -> tuple[list[VisitDay], Counter]:
    """Collapse qualifying service records into one visit-day per customer/date."""
    identity_map = identity_map or {}
    unresolved_ids = set(unresolved_ids or set())
    excluded: Counter = Counter()
    groups: dict = {}
    non_collapsed_index = 0

    for row in records or []:
        normalized = _normalize_row(row, config, identity_map, unresolved_ids)
        if normalized is None or normalized["visit_date"] is None:
            excluded["missing_customer_or_date"] += 1
            continue
        if not _is_qualifying(normalized, config):
            excluded["not_completed"] += 1
            continue
        if cutoff is not None and normalized["visit_date"] > cutoff:
            excluded["after_cutoff"] += 1
            continue

        brand = str(normalized["brand"]) if normalized["brand"] else config.unknown_label
        if config.collapse_same_day_visits:
            key = (brand, normalized["customer"], normalized["visit_date"])
        else:
            non_collapsed_index += 1
            key = (brand, normalized["customer"], normalized["visit_date"], non_collapsed_index)

        bucket = groups.get(key)
        if bucket is None:
            bucket = {"outlets": set(), "therapists": set(), "sessions": 0, "unresolved": False}
            groups[key] = bucket
        if normalized["outlet"]:
            bucket["outlets"].add(str(normalized["outlet"]))
        if normalized["therapist"]:
            bucket["therapists"].add(str(normalized["therapist"]))
        bucket["sessions"] += 1
        bucket["unresolved"] = bucket["unresolved"] or normalized["unresolved"]

    visit_days: list[VisitDay] = []
    for key, bucket in groups.items():
        visit_days.append(
            VisitDay(
                brand_id=key[0],
                customer_id=key[1],
                visit_date=key[2],
                outlets=tuple(sorted(bucket["outlets"])),
                therapists=tuple(sorted(bucket["therapists"])),
                sessions=bucket["sessions"],
                unresolved_identity=bucket["unresolved"],
            )
        )
    visit_days.sort(key=lambda d: (d.brand_id, d.customer_id, d.visit_date))
    return visit_days, excluded


def _history_confidence(brand: str, first_visit: date, config: RetentionConfig) -> str:
    brand_coverage = (config.coverage or {}).get(brand) or {}
    start = _as_local_date(brand_coverage.get("reliable_history_start_date"), config.timezone)
    if start is None:
        return "unknown"
    return "complete" if first_visit >= start else "partial"


def build_customer_summaries(
    visit_days: list[VisitDay], config: RetentionConfig, cutoff: date | None = None
) -> list[CustomerSummary]:
    grouped: dict[tuple[str, str], list[VisitDay]] = defaultdict(list)
    for day in visit_days:
        grouped[(day.brand_id, day.customer_id)].append(day)

    summaries: list[CustomerSummary] = []
    for (brand, customer), days in grouped.items():
        days.sort(key=lambda d: d.visit_date)
        first = days[0]
        later = [day for day in days if day.visit_date > first.visit_date]
        second = later[0] if later else None

        origin_outlet = first.outlets[0] if len(first.outlets) == 1 else None
        origin_therapist = first.therapists[0] if len(first.therapists) == 1 else None

        first_return_outlet = None
        first_return_therapist = None
        if origin_outlet is not None:
            first_return_outlet = next((d.visit_date for d in later if origin_outlet in d.outlets), None)
        if origin_therapist is not None:
            first_return_therapist = next(
                (d.visit_date for d in later if origin_therapist in d.therapists), None
            )

        summaries.append(
            CustomerSummary(
                brand_id=brand,
                customer_id=customer,
                cohort_month=_month_key(first.visit_date),
                first_visit_date=first.visit_date,
                originating_outlet=first.outlet_label,
                originating_therapist=first.therapist_label,
                last_visit_date=days[-1].visit_date,
                total_visit_days=len(days),
                second_visit_date=second.visit_date if second else None,
                days_to_second_visit=(second.visit_date - first.visit_date).days if second else None,
                destination_outlet=second.outlet_label if second else None,
                destination_therapist=second.therapist_label if second else None,
                first_return_to_originating_outlet=first_return_outlet,
                first_return_to_originating_therapist=first_return_therapist,
                first_visit_history_confidence=_history_confidence(brand, first.visit_date, config),
                available_followup_days=(cutoff - first.visit_date).days if cutoff else None,
                unresolved_identity=any(day.unresolved_identity for day in days),
            )
        )
    summaries.sort(key=lambda s: (s.brand_id, s.cohort_month, s.customer_id))
    return summaries


def analyse(
    records,
    config: RetentionConfig | None = None,
    cutoff: date | str | None = None,
    identity_map: dict | None = None,
    unresolved_ids=None,
) -> RetentionResult:
    """Run the full pipeline and return a :class:`RetentionResult`."""
    config = config or RetentionConfig()
    cutoff_date = _as_local_date(cutoff, config.timezone) if cutoff else None

    visit_days, excluded = build_visit_days(
        records, config, identity_map=identity_map, unresolved_ids=unresolved_ids, cutoff=cutoff_date
    )
    if cutoff_date is None:
        cutoff_date = max((day.visit_date for day in visit_days), default=None)

    summaries = build_customer_summaries(visit_days, config, cutoff_date)
    if config.require_confirmed_history:
        summaries = [s for s in summaries if s.first_visit_history_confidence == "complete"]

    return RetentionResult(
        config=config, cutoff=cutoff_date, visit_days=visit_days, summaries=summaries, excluded=excluded
    )


def scope_result(
    result: RetentionResult, brand: str | None = None, cohort_months=None
) -> RetentionResult:
    """Return a view of the result limited to one brand and/or cohort months.

    All visit-days of the selected customers are preserved (a customer's later
    visits are needed to compute monthly retention and returns), but customers
    outside the selection are dropped entirely.
    """
    months = set(cohort_months) if cohort_months else None
    summaries = [
        summary
        for summary in result.summaries
        if (brand is None or summary.brand_id == brand)
        and (months is None or summary.cohort_month in months)
    ]
    keys = {(summary.brand_id, summary.customer_id) for summary in summaries}
    visit_days = [day for day in result.visit_days if (day.brand_id, day.customer_id) in keys]
    return RetentionResult(
        config=result.config,
        cutoff=result.cutoff,
        visit_days=visit_days,
        summaries=summaries,
        excluded=result.excluded,
    )


# ---------------------------------------------------------------------------
# Derived tables
# ---------------------------------------------------------------------------


def _group_by_cohort(summaries):
    grouped: dict[str, list[CustomerSummary]] = defaultdict(list)
    for summary in summaries:
        grouped[summary.cohort_month].append(summary)
    return grouped


def second_visit_conversion(
    result: RetentionResult, brand: str | None = None, cohort_months=None, windows=None
) -> list[dict]:
    """Table A - cumulative second-visit conversion within each window."""
    result = scope_result(result, brand, cohort_months)
    config = result.config
    windows = tuple(windows or config.windows)
    grouped = _group_by_cohort(result.summaries)
    months = sorted(cohort_months) if cohort_months else sorted(grouped)

    if not months:
        placeholder = {"cohort_month": "(no eligible customers)", "customers": 0, "small_sample": 0}
        for window in windows:
            placeholder[f"days_{window}"] = "Not applicable"
            placeholder[f"days_{window}_pct"] = None
            placeholder[f"days_{window}_returned"] = None
            placeholder[f"maturity_{window}"] = "not_applicable"
        return [placeholder]

    rows: list[dict] = []
    for month in months:
        members = grouped.get(month, [])
        size = len(members)
        latest_first = max((member.first_visit_date for member in members), default=None)
        row = {
            "cohort_month": month,
            "customers": size,
            "small_sample": int(size < config.small_sample_threshold),
        }
        for window in windows:
            key = f"days_{window}"
            if size == 0:
                row[key] = "Not applicable"
                row[f"{key}_pct"] = None
                row[f"{key}_returned"] = None
                row[f"maturity_{window}"] = "not_applicable"
            elif (
                latest_first is not None
                and result.cutoff is not None
                and latest_first + timedelta(days=window) > result.cutoff
            ):
                row[key] = "Not yet mature"
                row[f"{key}_pct"] = None
                row[f"{key}_returned"] = None
                row[f"maturity_{window}"] = "not_yet_mature"
            else:
                returned = sum(
                    1
                    for member in members
                    if member.days_to_second_visit is not None
                    and 1 <= member.days_to_second_visit <= window
                )
                pct = _pct(returned, size)
                row[key] = f"{pct:.1f}% ({returned}/{size})"
                row[f"{key}_pct"] = pct
                row[f"{key}_returned"] = returned
                row[f"maturity_{window}"] = "completed"
        rows.append(row)
    return rows


def _has_active_visit(day: VisitDay, summary: CustomerSummary, cal_month: str, scope: str, config) -> bool:
    if _month_key(day.visit_date) != cal_month:
        return False
    if scope == "outlet":
        origin = summary.originating_outlet
        if origin in (config.unknown_label, config.multiple_label):
            return False
        return origin in day.outlets
    if scope == "therapist":
        origin = summary.originating_therapist
        if origin in (config.unknown_label, config.multiple_label):
            return False
        return origin in day.therapists
    return True  # brand scope: any qualifying visit within the brand


def monthly_cohort_retention(
    result: RetentionResult,
    scope: str = "brand",
    brand: str | None = None,
    cohort_months=None,
    max_months: int | None = None,
    include_month_0: bool | None = None,
) -> list[dict]:
    """Table B - monthly cohort retention (not cumulative)."""
    result = scope_result(result, brand, cohort_months)
    config = result.config
    scope = scope or "brand"
    include_month_0 = config.include_month_0 if include_month_0 is None else include_month_0
    max_months = max_months or config.max_retention_months
    grouped = _group_by_cohort(result.summaries)
    months = sorted(cohort_months) if cohort_months else sorted(grouped)
    visit_days = result.visit_days_by_customer()

    start_index = 0 if include_month_0 else 1
    last_completed: dict[str, int] = {}
    rows: list[dict] = []

    for month in months:
        members = grouped.get(month, [])
        size = len(members)
        row = {
            "cohort_month": month,
            "customers": size,
            "small_sample": int(size < config.small_sample_threshold),
        }
        index = start_index
        while index <= max_months:
            cal_month = _month_add(month, index)
            if result.cutoff is not None and _month_end(cal_month) > result.cutoff:
                break
            if index == 0:
                row["month_0"] = "Cohort membership (100.0%)" if size else "Not applicable"
                row["month_0_pct"] = 100.0 if size else None
                row["month_0_retained"] = size
            else:
                retained = sum(
                    1
                    for member in members
                    if any(
                        _has_active_visit(day, member, cal_month, scope, config)
                        for day in visit_days.get((member.brand_id, member.customer_id), [])
                    )
                )
                pct = _pct(retained, size)
                row[f"month_{index}"] = f"{pct:.1f}% ({retained}/{size})" if size else "Not applicable"
                row[f"month_{index}_pct"] = pct
                row[f"month_{index}_retained"] = retained
            index += 1
        last_completed[month] = index - 1
        rows.append(row)

    final_index = max(last_completed.values()) if last_completed else 0
    for row in rows:
        for index in range(start_index, final_index + 1):
            row.setdefault(f"month_{index}", "")
            row.setdefault(f"month_{index}_pct", None)
            row.setdefault(f"month_{index}_retained", None)
    return rows


def _returned_within(summary: CustomerSummary, window: int) -> bool:
    return summary.days_to_second_visit is not None and 1 <= summary.days_to_second_visit <= window


def drill_down(
    result: RetentionResult,
    dimension: str = "outlet",
    window: int | None = None,
    brand: str | None = None,
    cohort_months=None,
) -> list[dict]:
    """Table C - originating outlet / therapist drill-down for a return window."""
    result = scope_result(result, brand, cohort_months)
    config = result.config
    window = int(window or config.max_window)
    dimension = dimension or "outlet"
    if dimension not in ("outlet", "therapist"):
        raise ValueError("dimension must be 'outlet' or 'therapist'")

    summaries = list(result.summaries)

    origin_of = (
        (lambda s: s.originating_outlet)
        if dimension == "outlet"
        else (lambda s: s.originating_therapist)
    )
    dest_of = (
        (lambda s: s.destination_outlet) if dimension == "outlet" else (lambda s: s.destination_therapist)
    )

    grouped: dict[str, list[CustomerSummary]] = defaultdict(list)
    for summary in summaries:
        grouped[origin_of(summary)].append(summary)

    def sort_key(origin: str):
        return (origin in (config.unknown_label, config.multiple_label), origin)

    rows: list[dict] = []
    for origin in sorted(grouped, key=sort_key):
        members = grouped[origin]
        eligible = len(members)
        unavailable = origin in (config.unknown_label, config.multiple_label)

        brand_return = sum(1 for s in members if _returned_within(s, window))
        if dimension == "outlet":
            same_provider = sum(
                1
                for s in members
                if s.days_to_return_originating_outlet is not None
                and 1 <= s.days_to_return_originating_outlet <= window
            )
        else:
            same_provider = sum(
                1
                for s in members
                if s.days_to_return_originating_therapist is not None
                and 1 <= s.days_to_return_originating_therapist <= window
            )

        dest_same = dest_diff = dest_unknown = no_return = 0
        for summary in members:
            if not _returned_within(summary, window):
                no_return += 1
                continue
            if unavailable:
                dest_unknown += 1
                continue
            destination = dest_of(summary)
            if destination in (config.unknown_label, config.multiple_label):
                dest_unknown += 1
            elif destination == origin:
                dest_same += 1
            else:
                dest_diff += 1

        rows.append(
            {
                "origin_group": origin,
                "dimension": dimension,
                "eligible": eligible,
                "return_window_days": window,
                "brand_return": brand_return,
                "brand_return_pct": _pct(brand_return, eligible),
                "brand_return_display": f"{_pct(brand_return, eligible):.1f}% ({brand_return}/{eligible})"
                if eligible
                else "Not applicable",
                "same_provider_return": None if unavailable else same_provider,
                "same_provider_return_pct": None if unavailable else _pct(same_provider, eligible),
                "same_provider_available": 0 if unavailable else 1,
                "second_visit_same_provider": dest_same,
                "second_visit_different_provider": dest_diff,
                "second_visit_unknown_or_multiple": dest_unknown,
                "no_brand_return": no_return,
                "second_visit_same_provider_pct": _pct(dest_same, eligible),
                "second_visit_different_provider_pct": _pct(dest_diff, eligible),
                "second_visit_unknown_or_multiple_pct": _pct(dest_unknown, eligible),
                "no_brand_return_pct": _pct(no_return, eligible),
                "small_sample": int(eligible < config.small_sample_threshold),
            }
        )
    return rows


def return_interval_distribution(result: RetentionResult, brand: str | None = None, cohort_months=None) -> list[dict]:
    """Distribution of time between the first and second visit (among returners)."""
    result = scope_result(result, brand, cohort_months)
    config = result.config
    returners = [
        s.days_to_second_visit
        for s in result.summaries
        if s.days_to_second_visit is not None and s.days_to_second_visit > 0
    ]
    total = len(returners)
    rows: list[dict] = []
    for low, high, label in config.interval_buckets:
        count = sum(1 for days in returners if low <= days <= high)
        rows.append(
            {
                "interval_bucket": label,
                "lower_days": low,
                "upper_days": high,
                "returning_customers": count,
                "share_of_returners_pct": round(100 * count / total, 1) if total else 0.0,
            }
        )
    return rows


def retention_headline(result: RetentionResult, brand: str | None = None, cohort_months=None, windows=None) -> list[dict]:
    """Headline metrics that belong with the interval distribution."""
    result = scope_result(result, brand, cohort_months)
    config = result.config
    windows = tuple(windows or config.windows)
    full_window = max(windows)

    summaries = result.summaries
    eligible = len(summaries)
    returners = [
        s.days_to_second_visit
        for s in summaries
        if s.days_to_second_visit is not None and s.days_to_second_visit > 0
    ]
    median_days = round(statistics.median(returners), 1) if returners else None

    observed = [
        s for s in summaries if s.available_followup_days is None or s.available_followup_days >= full_window
    ]
    observed_count = len(observed)
    returned_in_window = sum(
        1
        for s in observed
        if s.days_to_second_visit is not None and 1 <= s.days_to_second_visit <= full_window
    )

    return [
        {
            "eligible_customers": eligible,
            "returning_customers": len(returners),
            "returning_pct": _pct(len(returners), eligible),
            "median_days_to_second_visit": median_days,
            "full_window_days": full_window,
            "observed_customers": observed_count,
            "returning_within_full_window": returned_in_window,
            "returning_within_full_window_pct": _pct(returned_in_window, observed_count),
            "no_return_within_full_window": observed_count - returned_in_window,
            "no_return_within_full_window_pct": _pct(observed_count - returned_in_window, observed_count),
        }
    ]


def data_quality(result: RetentionResult, brand: str | None = None, cohort_months=None) -> list[dict]:
    """Data-quality indicators shown next to the cohort tables."""
    result = scope_result(result, brand, cohort_months)
    config = result.config
    scoped = list(result.summaries)

    immature_windows = 0
    if result.cutoff is not None:
        for month in {s.cohort_month for s in scoped}:
            latest_first = max(
                (s.first_visit_date for s in scoped if s.cohort_month == month), default=None
            )
            if latest_first is None:
                continue
            immature_windows += sum(
                1 for window in config.windows if latest_first + timedelta(days=window) > result.cutoff
            )

    small_cohorts = sum(
        1
        for month in {s.cohort_month for s in scoped}
        if sum(1 for s in scoped if s.cohort_month == month) < config.small_sample_threshold
    )

    unknown = config.unknown_label
    multiple = config.multiple_label
    rows = [
        ("Data complete through", result.cutoff.isoformat() if result.cutoff else "Unknown",
         "Observation cutoff used for maturity."),
        ("Eligible customers", len(scoped), "Confirmed new / first-observed customers in scope."),
        ("Returning customers", sum(1 for s in scoped if s.brand_retained),
         "Customers with a later qualifying brand visit."),
        ("Unresolved identities", sum(1 for s in scoped if s.unresolved_identity),
         "Excluded from confirmed new-customer cohorts until resolved."),
        ("Unknown originating therapist", sum(1 for s in scoped if s.originating_therapist == unknown),
         "First visit-day without a therapist assignment."),
        ("Multiple originating therapists", sum(1 for s in scoped if s.originating_therapist == multiple),
         "First visit-day involving several therapists."),
        ("Unknown originating outlet", sum(1 for s in scoped if s.originating_outlet == unknown),
         "First visit-day without an outlet."),
        ("Multiple originating outlets", sum(1 for s in scoped if s.originating_outlet == multiple),
         "First visit-day spanning several outlets."),
        ("Incomplete history coverage",
         sum(1 for s in scoped if s.first_visit_history_confidence in ("partial", "unknown")),
         "Label these as first-observed visit cohorts, not confirmed new."),
        ("Confirmed new customers",
         sum(1 for s in scoped if s.first_visit_history_confidence == "complete"),
         "First observed visit after the reliable history start date."),
        ("Immature cohort windows", immature_windows, "Cells displayed as Not yet mature."),
        ("Cohorts below reporting threshold", small_cohorts,
         f"Reporting threshold: {config.small_sample_threshold} customers."),
        ("Excluded - not completed", result.excluded.get("not_completed", 0),
         "Cancellations, no-shows and other non-qualifying visits."),
        ("Excluded - missing customer or date", result.excluded.get("missing_customer_or_date", 0),
         "Records that could not be attributed to a customer/date."),
        ("Excluded - after observation cutoff", result.excluded.get("after_cutoff", 0),
         "Visits beyond the data-complete-through date."),
    ]
    return [{"indicator": indicator, "value": str(value), "note": note} for indicator, value, note in rows]


def customer_summary_rows(result: RetentionResult, brand: str | None = None, cohort_months=None) -> list[dict]:
    """Flat rendering of the derived one-row-per-customer-per-brand summary."""
    result = scope_result(result, brand, cohort_months)
    rows: list[dict] = []
    for summary in result.summaries:
        rows.append(
            {
                "brand_id": summary.brand_id,
                "customer_id": summary.customer_id,
                "cohort_month": summary.cohort_month,
                "first_visit_date": summary.first_visit_date.isoformat(),
                "originating_outlet": summary.originating_outlet,
                "originating_therapist": summary.originating_therapist,
                "second_visit_date": (
                    summary.second_visit_date.isoformat() if summary.second_visit_date else None
                ),
                "destination_outlet": summary.destination_outlet,
                "destination_therapist": summary.destination_therapist,
                "first_return_to_originating_outlet": (
                    summary.first_return_to_originating_outlet.isoformat()
                    if summary.first_return_to_originating_outlet
                    else None
                ),
                "first_return_to_originating_therapist": (
                    summary.first_return_to_originating_therapist.isoformat()
                    if summary.first_return_to_originating_therapist
                    else None
                ),
                "last_visit_date": summary.last_visit_date.isoformat(),
                "total_visit_days": summary.total_visit_days,
                "days_to_second_visit": summary.days_to_second_visit,
                "first_visit_history_confidence": summary.first_visit_history_confidence,
                "available_followup_days": summary.available_followup_days,
                "brand_retained": int(summary.brand_retained),
                "unresolved_identity": int(summary.unresolved_identity),
            }
        )
    return rows


