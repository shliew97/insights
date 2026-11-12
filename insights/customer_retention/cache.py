# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""File cache for the customer retention backend functions.

The retention numbers are expensive to compute (every booking is loaded and
analysed), so they are precomputed once a day by :func:`refresh_all` (scheduled
at 05:30, see ``hooks.py``) and stored as JSON files in the site's private
folder. The backend functions read these files first and only compute when no
file exists for the requested args.

To refresh manually (same as the scheduled job)::

    bench --site <site> execute insights.customer_retention.cache.refresh_all
"""

from __future__ import annotations

import hashlib
import json
import os

import frappe

CACHE_FOLDER = "customer_retention_cache"
FUNCTION_PATH_PREFIX = "insights.customer_retention."
REFRESH_FLAG = "customer_retention_refresh"


def cache_dir() -> str:
    return frappe.get_site_path("private", CACHE_FOLDER)


def _cache_file(function_name: str, args: dict) -> str:
    payload = json.dumps(args or {}, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode()).hexdigest()[:16]
    return os.path.join(cache_dir(), f"{function_name}-{digest}.json")


def read(function_name: str, args: dict):
    """Return ``(rows, columns)`` from the cache file, or ``None`` when there is none."""
    path = _cache_file(function_name, args)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data["rows"], data.get("columns") or []
    except (OSError, ValueError, KeyError):
        return None


def write(function_name: str, args: dict, rows, columns) -> str:
    """Write the result atomically, so a reader never sees a half written file."""
    os.makedirs(cache_dir(), exist_ok=True)
    path = _cache_file(function_name, args)
    tmp_path = f"{path}.{os.getpid()}.tmp"
    document = {
        "function": function_name,
        "args": args,
        "generated_at": str(frappe.utils.now()),
        "columns": list(columns or []),
        "rows": list(rows),
    }
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(frappe.as_json(document, indent=None))
    os.replace(tmp_path, path)
    return path


def refresh_all():
    """Recompute and store the result of every retention backend-function query.

    Scheduled daily at 05:30. Files that no longer belong to any query (e.g. after
    its args were changed) are removed once the refresh has finished.
    """
    queries = frappe.get_all(
        "Insights Query v3",
        filters={"is_backend_function": 1, "backend_function_path": ["like", f"{FUNCTION_PATH_PREFIX}%"]},
        fields=["name", "backend_function_path", "backend_function_args"],
    )

    written: set[str] = set()
    seen: set[tuple[str, str]] = set()
    frappe.flags[REFRESH_FLAG] = True
    try:
        for query in queries:
            try:
                args = frappe.parse_json(query.backend_function_args) if query.backend_function_args else {}
                if not isinstance(args, dict):
                    raise ValueError("Backend Function Args must be a JSON object")
                key = (query.backend_function_path, json.dumps(args, sort_keys=True, default=str))
                if key in seen:
                    continue
                seen.add(key)
                frappe.call(query.backend_function_path, **args)
                function_name = query.backend_function_path.rsplit(".", 1)[-1]
                written.add(os.path.basename(_cache_file(function_name, args)))
            except Exception:
                frappe.log_error(
                    title="Customer retention cache refresh failed",
                    message=f"{query.name} ({query.backend_function_path})\n\n{frappe.get_traceback()}",
                )
    finally:
        frappe.flags[REFRESH_FLAG] = False

    # drop stale files only for queries that refreshed fine
    if os.path.isdir(cache_dir()) and written:
        for name in os.listdir(cache_dir()):
            if name.endswith(".json") and name not in written:
                os.remove(os.path.join(cache_dir(), name))
    return sorted(written)
