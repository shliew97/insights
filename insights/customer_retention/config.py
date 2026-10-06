# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Configuration for the customer retention cohort engine.

Every business rule from the developer brief that the marketing team may want
to tune is expressed here as a configuration value with a documented default.
The field names in the brief (``brand_id``, ``customer_id`` ...) are
illustrative; the engine works on this normalized configuration instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Cumulative return windows (local calendar days from the first visit-date).
DEFAULT_WINDOWS: tuple[int, ...] = (30, 60, 90, 180)

# Retention scopes: return to the same brand, the originating outlet or the
# originating therapist (same brand, including across outlets).
DEFAULT_SCOPES: tuple[str, ...] = ("brand", "outlet", "therapist")

# Malaysia time is the reporting timezone in the brief.
DEFAULT_TIMEZONE = "Asia/Kuala_Lumpur"

# Documented reporting threshold for flagging small samples.
DEFAULT_SMALL_SAMPLE_THRESHOLD = 30

UNKNOWN = "Unknown"
MULTIPLE = "Multiple"

# A "qualifying visit" is a completed service visit. Cancellations, no-shows,
# future bookings and purchases without attendance are excluded.
COMPLETED_STATUSES = frozenset({"completed"})
EXCLUDED_STATUSES = frozenset(
    {
        "cancelled",
        "canceled",
        "no-show",
        "no show",
        "noshow",
        "expired",
        "deleted",
        "refunded",
    }
)

# First-to-second visit interval buckets (inclusive bounds). The last bucket
# is capped at the largest return window per the brief.
DEFAULT_INTERVAL_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (1, 14, "1-14 days"),
    (15, 30, "15-30 days"),
    (31, 60, "31-60 days"),
    (61, 90, "61-90 days"),
    (91, 180, "91-180 days"),
)

# Source statuses that mean a service was actually delivered.
DEFAULT_ATTENDED_VALUES = frozenset({"completed", "in service", "served", "attended"})


@dataclass(slots=True)
class RetentionConfig:
    """Tunable business rules for the cohort retention engine."""

    windows: tuple[int, ...] = DEFAULT_WINDOWS
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    timezone: str = DEFAULT_TIMEZONE
    small_sample_threshold: int = DEFAULT_SMALL_SAMPLE_THRESHOLD

    completed_statuses: frozenset[str] = COMPLETED_STATUSES
    excluded_statuses: frozenset[str] = EXCLUDED_STATUSES

    interval_buckets: tuple[tuple[int, int, str], ...] = DEFAULT_INTERVAL_BUCKETS

    unknown_label: str = UNKNOWN
    multiple_label: str = MULTIPLE

    # Table B: whether to render Month 0 (label it as cohort membership).
    include_month_0: bool = False
    # Upper bound on the number of monthly columns for Table B.
    max_retention_months: int = 12

    # The brief's "same-day revisits count as one visit-day" rule. Turning this
    # off means every booking/session on the same day counts separately.
    collapse_same_day_visits: bool = True

    # Provenance: when a brand's earlier history is incomplete the results must
    # be labelled "first observed visit cohorts" instead of confirmed new
    # customers. ``require_confirmed_history`` drops partial/unknown cohorts.
    require_confirmed_history: bool = False

    # Extra per-brand coverage (reliable_history_start_date, ...) used to derive
    # the first-visit history confidence. Keyed by brand id.
    coverage: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalise tuple-able inputs so callers can pass lists from JSON.
        self.windows = tuple(sorted({int(w) for w in self.windows if int(w) > 0}))
        if not self.windows:
            self.windows = DEFAULT_WINDOWS
        self.scopes = tuple(self.scopes) or DEFAULT_SCOPES
        self.interval_buckets = tuple(
            (int(lo), int(hi), str(label)) for lo, hi, label in self.interval_buckets
        )

    @property
    def max_window(self) -> int:
        return max(self.windows)

    def with_overrides(self, **kwargs) -> RetentionConfig:
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean) if clean else self

    @classmethod
    def from_dict(cls, data: dict | None) -> RetentionConfig:
        data = dict(data or {})
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})
