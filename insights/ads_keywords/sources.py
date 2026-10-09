# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Keyword input adapters for the ads-keyword classifier.

The backend function accepts keywords in whichever shape is convenient:

* a JSON list of strings - ``["massage near me", "massage chair"]``
* a JSON list of objects - ``[{"keyword": "...", "search_volume": 5400}]``
* a pasted CSV / plain-text blob (one keyword per line, optional
  ``search volume`` and ``cost`` columns; a header row is auto-detected)

When nothing is supplied the backend function pulls the live keywords straight
from the Google Ads API (see :mod:`insights.ads_keywords.google_ads`); this
module only normalises keywords that are passed in explicitly.
"""

from __future__ import annotations

import csv
import io

_KEYWORD_HEADERS = ("keyword", "keywords", "term", "terms", "search term", "search_term", "query")
_VOLUME_HEADERS = ("search volume", "search_volume", "volume", "impressions", "clicks")
_COST_HEADERS = ("cost", "spend", "cpc", "cost (usd)", "cost_micros", "avg cpc")

__all__ = ["parse_keywords"]


def _first_index(header: list[str], names) -> int | None:
    for i, cell in enumerate(header):
        if cell in names:
            return i
    return None


def _to_number(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _row_number(row: list[str], index: int | None):
    if index is None or index >= len(row):
        return None
    return _to_number(row[index])


def _parse_text(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []

    rows = [row for row in csv.reader(io.StringIO(text)) if any((cell or "").strip() for cell in row)]
    if not rows:
        return []

    header = [(cell or "").strip().lower() for cell in rows[0]]
    has_header = any(cell in _KEYWORD_HEADERS for cell in header)

    if has_header:
        keyword_i = _first_index(header, _KEYWORD_HEADERS)
        volume_i = _first_index(header, _VOLUME_HEADERS)
        cost_i = _first_index(header, _COST_HEADERS)
        data = rows[1:]
    else:
        keyword_i, volume_i, cost_i = 0, 1, 2
        data = rows

    records: list[dict] = []
    for row in data:
        if keyword_i is None or keyword_i >= len(row):
            continue
        keyword = (row[keyword_i] or "").strip()
        if not keyword:
            continue
        record = {"keyword": keyword}
        volume = _row_number(row, volume_i)
        if volume is not None:
            record["search_volume"] = volume
        cost = _row_number(row, cost_i)
        if cost is not None:
            record["cost"] = cost
        records.append(record)
    return records


def parse_keywords(value) -> list[dict]:
    """Normalise any supported keyword input into a list of records."""
    if value in (None, ""):
        return []

    if isinstance(value, str):
        stripped = value.strip()
        # A JSON list smuggled in as a string (e.g. from a CSV arg).
        if stripped[:1] in "[{":
            import json

            try:
                return parse_keywords(json.loads(stripped))
            except ValueError:
                pass
        return _parse_text(value)

    if isinstance(value, (list, tuple)):
        records: list[dict] = []
        for item in value:
            if isinstance(item, dict):
                keyword = item.get("keyword") or item.get("term") or item.get("query")
                if keyword in (None, ""):
                    continue
                record = {"keyword": str(keyword).strip()}
                for key in ("search_volume", "impressions", "cost", "clicks", "conversions"):
                    if item.get(key) is not None:
                        record[key] = item[key]
                records.append(record)
            elif item not in (None, ""):
                records.append({"keyword": str(item).strip()})
        return records

    return []

