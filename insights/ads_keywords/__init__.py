# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Google Ads keyword intent analysis.

A peer of :mod:`insights.customer_retention`: it classifies Google Ads search
keywords so the marketing team can find high-intent terms to bid on and the
irrelevant / negative terms that waste spend.

The layers mirror the customer-retention package so the two features feel the
same to maintain:

* :mod:`insights.ads_keywords.config` - intent taxonomy and
  every tunable signal term.
* :mod:`insights.ads_keywords.engine` - the pure, rule-based
  classifier (imports nothing from ``frappe``, fully unit-tested).
* :mod:`insights.ads_keywords.google_ads` - REST adapter that fetches the real
  keywords from the Google Ads API.
* :mod:`insights.ads_keywords.sources` - parses explicitly passed keyword input
  (a list or a CSV/plain-text blob).
* :mod:`insights.ads_keywords.backend_functions` -
  whitelisted entry points exposed as ``Insights Query v3`` backend functions.
* :mod:`insights.ads_keywords.setup` - seeds the workbook,
  charts and dashboard into Insights.
"""

from insights.ads_keywords.config import KeywordIntentConfig

__all__ = ["KeywordIntentConfig"]
