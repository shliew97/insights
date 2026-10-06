# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Customer Retention cohort dashboard.

This package implements the *business logic* described in the marketing
"Customer Retention Dashboard Developer Brief" as reusable, testable
functionality that lives inside the Insights app.

The brief describes a logical dataset (``brand_id``, ``customer_id``,
``visit_id`` ...). Field names there are illustrative - the engine therefore
works on a small, documented logical schema and the concrete mapping onto a
real source (HealthLand POS ``Booking`` / ``Member`` / ``Outlet`` / ``Staff``)
is isolated in :mod:`insights.customer_retention.sources`.

Public entry points:

* :mod:`insights.customer_retention.engine` - the pure cohort engine
  (no ``frappe`` import, fully unit-testable).
* :mod:`insights.customer_retention.backend_functions` - whitelisted functions
  that expose the engine as ``Insights Query v3`` *backend function* queries.
* :mod:`insights.customer_retention.setup` - seeds the workbook / charts /
  dashboard into Insights.
"""

from insights.customer_retention.config import RetentionConfig

__all__ = ["RetentionConfig"]
