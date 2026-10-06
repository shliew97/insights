# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Create the Customer Retention dashboard on every site after deploy.

Shipped as a patch so that a fresh ``bench migrate`` (after pulling this app)
materialises the workbook, its backend-function queries, charts and the
dashboard without any manual step - no need to build it by hand in a workbook.

The seeder is idempotent by workbook title, so re-running is safe and sites that
already have it are left untouched.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "Insights Workbook"):
        return

    from insights.customer_retention.setup import seed_customer_retention_dashboard

    try:
        seed_customer_retention_dashboard()
    except Exception:
        frappe.log_error(title="Customer Retention dashboard seed failed")
