# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Tiny cache helpers.

The dashboard re-runs the same expensive work on every refresh, so results of
the two slow, quota-limited steps are cached:

* resolved Google Ads API version (saves a probe request per run),
* Google Ads keyword/metrics responses (saves API quota).

The classifier itself is pure CPU over a few hundred short strings, so it is
never cached (and never needs to be).

Everything is best-effort: if the cache is unavailable the caller just does the
work again. A TTL of ``0`` disables a cache.
"""

from __future__ import annotations

import hashlib

import frappe

PREFIX = "ads_keywords:"


def make_key(*parts) -> str:
    """Stable, short cache key for arbitrary parts."""
    raw = "|".join(str(part) for part in parts)
    return PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get(key: str):
    try:
        # expires=True keeps the read out of frappe.local.cache, which would
        # otherwise memoise the first (often None) read for the whole process.
        return frappe.cache().get_value(key, expires=True)
    except Exception:
        return None


def put(key: str, value, ttl: int | None) -> None:
    if not ttl:
        return
    try:
        frappe.cache().set_value(key, value, expires_in_sec=int(ttl))
    except Exception:
        pass


def conf_int(key: str, default: int) -> int:
    """Read an integer site-config value (falls back to ``default``)."""
    try:
        value = (frappe.conf or {}).get(key)
    except Exception:
        value = None
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
