# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Create the Google Ads keyword analysis dashboard on every site after deploy.

Shipped as a patch so a fresh ``bench migrate`` (after pulling this app)
materialises the workbook, its backend-function queries, charts and the
dashboard without any manual step.

The seeder is idempotent by workbook title, so re-running is safe and sites that
already have it are left untouched.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "Insights Workbook"):
        return

    from insights.ads_keywords.setup import seed_google_ads_keyword_dashboard

    try:
        seed_google_ads_keyword_dashboard()
    except Exception:
        frappe.log_error(title="Google Ads keyword dashboard seed failed")
