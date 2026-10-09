# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GoogleAdsSettings(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF

        api_version: DF.Data | None
        client_id: DF.Data | None
        client_secret: DF.Password | None
        customer_id: DF.Data | None
        default_date_range: DF.Data | None
        default_resource: DF.Literal["search_term_view", "keyword_view"]
        developer_token: DF.Password | None
        enabled: DF.Check
        login_customer_id: DF.Data | None
        max_rows: DF.Int
        refresh_token: DF.Password | None
    # end: auto-generated types


@frappe.whitelist()
def test_connection():
    """Whitelisted so the Google Ads Settings form button can validate credentials.

    Returns ``{"ok": bool, "message": str}``.
    """
    from insights.ads_keywords.google_ads import test_connection as _test_connection

    return _test_connection()


@frappe.whitelist()
def list_accounts():
    """List the Google Ads accounts the configured login can access (best effort)."""
    from insights.ads_keywords import google_ads

    creds = google_ads.get_credentials()
    if not google_ads.is_configured(creds):
        return {"ok": False, "message": "Google Ads API is not configured yet."}

    try:
        accessible = google_ads.list_accessible_customers()
    except Exception as error:
        return {"ok": False, "message": f"Could not list accounts: {error}"}

    try:
        children = google_ads.list_accounts()
    except Exception:
        children = []

    lines = ["<b>Accessible customer IDs:</b> " + ", ".join(accessible or ["(none)"])]
    for child in children:
        lines.append(
            f"{child.get('id')} - {child.get('name')} - manager={child.get('manager')} - "
            f"{child.get('status')}"
        )
    return {
        "ok": True,
        "message": "<br>".join(lines),
        "accessible": accessible,
        "children": children,
    }


@frappe.whitelist()
def get_refresh_token_url():
    """Consent URL behind the *Get Refresh Token* button (see ``oauth_flow``)."""
    from insights.ads_keywords import oauth_flow

    return oauth_flow.desk_authorize_url()


@frappe.whitelist()
def consume_refresh_token_message():
    """Message the OAuth callback left behind, shown once after the redirect."""
    from insights.ads_keywords import oauth_flow

    return oauth_flow.consume_flash()


@frappe.whitelist()
def get_local_refresh_token_url():
    """Consent URL that returns to localhost instead - no Google Cloud change.

    Google compares redirect URIs character for character, so the form callback
    needs registering first. This variant reuses the loopback URI the CLI helper
    already registers (``http://localhost:8080/``); the bench answers that port
    itself and sends the browser back to this form.
    """
    from insights.ads_keywords import oauth_flow

    return oauth_flow.local_authorize_url()

