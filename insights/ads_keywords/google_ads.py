# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Google Ads API adapter - REST only, no extra pip package required.

Credentials are read (in order) from:

1. the **Google Ads Settings** single doctype - Desk -> "Google Ads Settings"
   (this is where you fill in the API credentials), or
2. the site config, if the doctype fields are empty::

       bench --site <site> set-config google_ads_developer_token "..."
       bench --site <site> set-config google_ads_client_id "..."
       bench --site <site> set-config google_ads_client_secret "..."
       bench --site <site> set-config google_ads_refresh_token "..."
       bench --site <site> set-config google_ads_login_customer_id "1234567890"
       bench --site <site> set-config google_ads_customer_id "1234567890"

An OAuth2 refresh token is exchanged for an access token at
``https://oauth2.googleapis.com/token`` and then the account's real keywords /
search terms (with metrics) are fetched via ``googleAds:searchStream``.
"""

from __future__ import annotations

import frappe
import requests

from insights.ads_keywords import cache

__all__ = [
    "get_credentials",
    "is_configured",
    "save_refresh_token",
    "fetch_keywords",
    "test_connection",
]

API_BASE = "https://googleads.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"

DEFAULT_API_VERSION = "v23"
DEFAULT_RESOURCE = "search_term_view"
DEFAULT_DATE_RANGE = "LAST_30_DAYS"
DEFAULT_LIMIT = 500
# Only return keywords that actually got impressions (set to 0 to also include
# zero-impression keywords - useful with test accounts, which never serve ads).
DEFAULT_MIN_IMPRESSIONS = 1

# Google sunset API versions roughly once a year (v19-v21 are already retired);
# when the configured version returns 404 we transparently retry these ones.
FALLBACK_API_VERSIONS = ("v23", "v22", "v24", "v25")

# How long to cache Google Ads API responses / the resolved API version, so a
# dashboard refresh does not re-spend API quota. Seconds; 0 disables caching.
DEFAULT_CACHE_TTL = 900

# When the configured Customer ID is a manager (MCC) account, its child advertiser
# accounts are queried too (up to this many) so the analysis works without listing
# every child account by hand.
MAX_CHILD_ACCOUNTS = 50

# The two useful grains for keyword analysis.
RESOURCES = ("search_term_view", "keyword_view")

# site-config fallbacks, used when the Settings form field is empty.
CONFIG_KEYS = {
    "developer_token": "google_ads_developer_token",
    "client_id": "google_ads_client_id",
    "client_secret": "google_ads_client_secret",
    "refresh_token": "google_ads_refresh_token",
    "login_customer_id": "google_ads_login_customer_id",
    "customer_id": "google_ads_customer_id",
    "api_version": "google_ads_api_version",
}

# Fields stored encrypted (Password fieldtype) on the settings doctype.
_PASSWORD_FIELDS = ("developer_token", "client_secret", "refresh_token")


def _clean_id(value) -> str:
    """Customer ids are pasted as 123-456-7890; the API wants the digits."""
    return str(value or "").replace("-", "").replace(" ", "").strip()


def _clean_value(value):
    """Trim stray whitespace/newlines pasted into credential fields.

    A trailing space or newline in a header value makes ``requests`` raise
    "Invalid leading whitespace ... in header value", so every credential is
    normalised here.
    """
    if value in (None, ""):
        return None
    text = str(value).replace("\r", "").replace("\n", "").strip()
    return text or None


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def get_credentials() -> dict:
    """Resolve Google Ads credentials from the settings form or site config."""
    creds: dict[str, object] = {field: None for field in CONFIG_KEYS}

    # 1) the Google Ads Settings form (preferred place to fill these in)
    if frappe.db.exists("DocType", "Google Ads Settings"):
        doc = frappe.get_cached_doc("Google Ads Settings")
        for field in CONFIG_KEYS:
            if field in _PASSWORD_FIELDS:
                value = doc.get_password(field, raise_exception=False)
            else:
                value = doc.get(field)
            creds[field] = _clean_value(value)

    # 2) site config fallback
    conf = getattr(frappe, "conf", None) or {}
    for field, key in CONFIG_KEYS.items():
        if not creds.get(field):
            creds[field] = _clean_value(conf.get(key))

    creds["api_version"] = (
        str(creds.get("api_version") or DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION
    )
    creds["customer_id"] = _clean_id(creds.get("customer_id"))
    creds["login_customer_id"] = _clean_id(creds.get("login_customer_id"))
    return creds


def is_configured(creds: dict | None = None) -> bool:
    """True when every credential needed for a query is present."""
    creds = creds or get_credentials()
    required = ("developer_token", "client_id", "client_secret", "refresh_token", "customer_id")
    return all(creds.get(field) for field in required)


def save_refresh_token(token: str) -> None:
    """Store ``token`` on the Google Ads Settings form.

    Written through the document (not ``set_single_value``, which would bypass
    the fieldtype) so the Password field keeps the value encrypted. The
    document cache is cleared afterwards, otherwise ``get_credentials`` would
    keep handing out the previous token for the rest of the request.
    """
    if not frappe.db.exists("DocType", "Google Ads Settings"):
        raise frappe.ValidationError("Google Ads Settings is not installed on this site.")

    doc = frappe.get_single("Google Ads Settings")
    doc.refresh_token = token
    doc.save(ignore_permissions=True)
    frappe.clear_cache(doctype="Google Ads Settings")
    frappe.db.commit()


def _access_token(creds: dict) -> str:
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code != 200:
        frappe.throw(f"Google Ads OAuth failed ({response.status_code}): {response.text}")
    return response.json()["access_token"]


def _headers(creds: dict, token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {str(token).strip()}",
        "developer-token": str(creds["developer_token"]).strip(),
        "Content-Type": "application/json",
    }
    # Required when the account is accessed through a manager (MCC) account.
    login_customer_id = str(creds.get("login_customer_id") or "").strip()
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    return headers


def _build_query(resource: str, date_range: str, limit: int, min_impressions: int = 1) -> str:
    if resource == "keyword_view":
        select = (
            "SELECT ad_group_criterion.keyword.text, "
            "ad_group_criterion.keyword.match_type, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
            "FROM keyword_view "
        )
    else:
        select = (
            "SELECT search_term_view.search_term, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
            "FROM search_term_view "
        )
    query = f"{select}WHERE segments.date DURING {date_range} "
    if min_impressions and int(min_impressions) > 0:
        query += f"AND metrics.impressions >= {int(min_impressions)} "
    query += "ORDER BY metrics.impressions DESC "
    if limit:
        query += f"LIMIT {int(limit)}"
    return query


def _extract(row: dict, resource: str) -> dict:
    if resource == "keyword_view":
        criterion = (row.get("adGroupCriterion") or {}).get("keyword") or {}
        keyword = criterion.get("text")
        match_type = criterion.get("matchType")
    else:
        keyword = (row.get("searchTermView") or {}).get("searchTerm")
        match_type = None

    metrics = row.get("metrics") or {}
    return {
        "keyword": (keyword or "").strip(),
        "match_type": match_type,
        "impressions": int(metrics.get("impressions", 0) or 0),
        "clicks": int(metrics.get("clicks", 0) or 0),
        "cost": round(int(metrics.get("costMicros", 0) or 0) / 1_000_000, 2),
        "conversions": round(float(metrics.get("conversions", 0) or 0), 2),
        "resource": resource,
    }


def _aggregate(rows: list[dict]) -> list[dict]:
    """Collapse rows into one row per unique keyword, summing metrics."""
    merged: dict[str, dict] = {}
    seen_accounts: dict[str, set] = {}
    for row in rows:
        key = row["keyword"].strip().lower()
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(row)
            seen_accounts[key] = set()
        else:
            target = merged[key]
            for metric in ("impressions", "clicks", "cost", "conversions"):
                target[metric] = round((target.get(metric) or 0) + (row.get(metric) or 0), 2)
        if row.get("account"):
            seen_accounts[key].add(row["account"])

    for key, row in merged.items():
        row["accounts"] = len(seen_accounts[key]) or 1
    return sorted(merged.values(), key=lambda row: row.get("impressions") or 0, reverse=True)


def _post(creds: dict, token: str, version: str, customer: str, query: str, timeout: int = 90):
    url = f"{API_BASE}/{version}/customers/{customer}/googleAds:searchStream"
    return requests.post(url, headers=_headers(creds, token), json={"query": query}, timeout=timeout)


def _resolve_api_version(creds: dict, token: str, customer: str) -> str:
    """Return the first API version the server accepts (cached for a while).

    Google sunsets old versions; a sunset/unpublished version path returns 404
    (often as an HTML page). Probe the configured version first, then nearby
    ones, and remember whichever the server answers.
    """
    key = cache.make_key("gads_version", creds.get("developer_token"))
    ttl = cache.conf_int("google_ads_cache_ttl", DEFAULT_CACHE_TTL)
    if ttl:
        cached = cache.get(key)
        if cached:
            return cached

    configured = creds.get("api_version") or DEFAULT_API_VERSION
    candidates = [configured] + [v for v in FALLBACK_API_VERSIONS if v != configured]
    probe = f"SELECT customer.id FROM customer WHERE customer.id = {customer}"
    resolved = configured
    for version in candidates:
        try:
            response = _post(creds, token, version, customer, probe, timeout=30)
        except Exception:
            continue
        # 404 -> this version path does not exist; try the next one.
        if response.status_code == 404:
            continue
        resolved = version
        break

    cache.put(key, resolved, ttl)
    return resolved


def _run_search(creds: dict, token: str, customer: str, query: str) -> list[dict]:
    """Run one ``searchStream`` request against a single customer."""
    response = _post(creds, token, creds["api_version"], customer, query)
    if response.status_code != 200:
        frappe.throw(
            f"Google Ads API error for customer {customer} ({response.status_code}): {response.text}"
        )

    payload = response.json()
    chunks = payload if isinstance(payload, list) else [payload]
    rows: list[dict] = []
    for chunk in chunks:
        rows.extend(chunk.get("results", []) or [])
    return rows


def _is_manager(creds: dict, token: str, customer: str) -> bool:
    """True when the customer is a manager (MCC) account."""
    query = f"SELECT customer.manager FROM customer WHERE customer.id = {customer} LIMIT 1"
    rows = _run_search(creds, token, customer, query)
    if not rows:
        return False
    return bool((rows[0].get("customer") or {}).get("manager"))


def _child_accounts(creds: dict, token: str, manager: str) -> list[str]:
    """Enabled advertiser (non-manager) accounts under a manager account."""
    query = (
        "SELECT customer_client.id, customer_client.manager, customer_client.status "
        "FROM customer_client "
        "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED'"
    )
    ids: list[str] = []
    for row in _run_search(creds, token, manager, query):
        client = row.get("customerClient") or {}
        account_id = client.get("id")
        if account_id and str(account_id) != str(manager):
            ids.append(str(account_id))
    return ids


def fetch_keywords(
    resource: str | None = None,
    customer_id: str | None = None,
    date_range: str | None = None,
    limit: int | None = None,
    min_impressions: int | None = None,
    expand_manager: bool = True,
    creds: dict | None = None,
) -> list[dict]:
    """Fetch real keywords / search terms with their metrics from Google Ads.

    ``resource`` is ``search_term_view`` (default; what users actually typed) or
    ``keyword_view`` (the keywords you bid on). ``date_range`` is any Google Ads
    date-range literal such as ``LAST_30_DAYS`` or ``LAST_7_DAYS``.

    A manager (MCC) account carries no keywords of its own, so when the configured
    Customer ID is a manager and ``expand_manager`` is true, the keywords of its
    child advertiser accounts are fetched and merged (one row per unique keyword).
    """
    creds = creds or get_credentials()
    if not is_configured(creds):
        frappe.throw(
            "Google Ads API is not configured. Fill in the <b>Google Ads Settings</b> form "
            "(search the Desk for 'Google Ads Settings') or set the <code>google_ads_*</code> "
            "site config keys.",
            title="Google Ads API not configured",
        )

    resource = resource or DEFAULT_RESOURCE
    if resource not in RESOURCES:
        frappe.throw(f"Unknown resource '{resource}'; expected one of {', '.join(RESOURCES)}")

    customer = _clean_id(customer_id) or creds["customer_id"]
    date_range = date_range or DEFAULT_DATE_RANGE
    limit = int(limit or DEFAULT_LIMIT)
    min_impressions = DEFAULT_MIN_IMPRESSIONS if min_impressions is None else int(min_impressions)
    query = _build_query(resource, date_range, limit, min_impressions)

    # Serve repeated dashboard refreshes from cache (no extra API quota).
    cache_ttl = cache.conf_int("google_ads_cache_ttl", DEFAULT_CACHE_TTL)
    cache_key = cache.make_key(
        "gads_keywords",
        creds.get("customer_id"),
        creds.get("login_customer_id"),
        customer,
        resource,
        date_range,
        limit,
        min_impressions,
        _as_bool(expand_manager, default=True),
    )
    if cache_ttl:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    token = _access_token(creds)
    creds["api_version"] = _resolve_api_version(creds, token, customer)

    targets = [customer]
    if _as_bool(expand_manager, default=True):
        is_manager = False
        try:
            is_manager = _is_manager(creds, token, customer)
        except Exception:
            frappe.log_error(title="Google Ads manager detection failed")

        if is_manager:
            try:
                children = _child_accounts(creds, token, customer)
            except Exception:
                children = []
                frappe.log_error(title="Google Ads child account lookup failed")

            if children:
                targets = children[:MAX_CHILD_ACCOUNTS]
            else:
                frappe.throw(
                    f"Customer ID <b>{customer}</b> is a manager (MCC) account and no advertiser "
                    "accounts are visible to this login. Metrics cannot be requested for a manager "
                    "account, so put an <b>advertiser account id</b> in Customer ID (with "
                    "<b>Login Customer ID</b> set to the manager id), or grant this Google account "
                    "access to the manager's client accounts.",
                    title="No advertiser accounts under this manager",
                )

    rows: list[dict] = []
    errors: list[Exception] = []
    for target in targets:
        try:
            raw_rows = _run_search(creds, token, target, query)
        except Exception as error:
            if len(targets) == 1:
                raise
            errors.append(error)
            frappe.log_error(title=f"Google Ads query failed for customer {target}")
            continue
        for item in raw_rows:
            record = _extract(item, resource)
            if record["keyword"]:
                record["account"] = target
                rows.append(record)

    # Every child failed - surface the first error instead of a silent empty result.
    if not rows and errors and len(targets) > 1:
        raise errors[0]
    result = _aggregate(rows)
    cache.put(cache_key, result, cache_ttl)
    return result



def test_connection() -> dict:
    """Validate the configured credentials (used by the Settings form button)."""
    creds = get_credentials()
    if not is_configured(creds):
        return {
            "ok": False,
            "message": "Not configured. Fill in Google Ads Settings (or the google_ads_* site config keys).",
        }
    try:
        token = _access_token(creds)
    except Exception as error:
        return {"ok": False, "message": f"OAuth failed: {error}"}

    creds["api_version"] = _resolve_api_version(creds, token, creds["customer_id"])
    query = (
        "SELECT customer.id, customer.descriptive_name FROM customer "
        f"WHERE customer.id = {creds['customer_id']} LIMIT 1"
    )
    try:
        response = _post(creds, token, creds["api_version"], creds["customer_id"], query, timeout=30)
    except Exception as error:
        return {"ok": False, "message": f"Request failed: {error}"}
    if response.status_code != 200:
        return {"ok": False, "message": f"API error {response.status_code}: {response.text}"}

    try:
        if _is_manager(creds, token, creds["customer_id"]):
            children = _child_accounts(creds, token, creds["customer_id"])
            if children:
                return {
                    "ok": True,
                    "message": (
                        f"Connected. Manager (MCC) account with {len(children)} advertiser "
                        "account(s); keywords will be pulled from all of them."
                    ),
                }
            return {
                "ok": True,
                "message": (
                    "Connected, but this manager (MCC) has no advertiser accounts visible to this "
                    "login. Put an advertiser account id in Customer ID (Login Customer ID = the "
                    "manager id), or grant this Google account access to the manager's client accounts."
                ),
            }
    except Exception:
        pass
    return {"ok": True, "message": "Connected to Google Ads successfully."}


def list_accounts(creds: dict | None = None) -> list[dict]:
    """List the accounts (id, name, manager flag) visible to the configured login."""
    creds = creds or get_credentials()
    if not is_configured(creds):
        frappe.throw("Google Ads API is not configured.")
    token = _access_token(creds)
    creds["api_version"] = _resolve_api_version(creds, token, creds["customer_id"])
    query = (
        "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, "
        "customer_client.status, customer_client.level FROM customer_client"
    )
    accounts: list[dict] = []
    for row in _run_search(creds, token, creds["customer_id"], query):
        client = row.get("customerClient") or {}
        accounts.append(
            {
                "id": client.get("id"),
                "name": client.get("descriptiveName"),
                "manager": bool(client.get("manager")),
                "status": client.get("status"),
                "level": client.get("level"),
            }
        )
    return accounts


def list_accessible_customers(creds: dict | None = None) -> list[str]:
    """All customer ids the configured login can access.

    Uses the REST ``customers:listAccessibleCustomers`` method, which is the
    quickest way to discover the advertiser account id to put in Customer ID.
    """
    creds = creds or get_credentials()
    if not is_configured(creds):
        frappe.throw("Google Ads API is not configured.")
    token = _access_token(creds)
    creds["api_version"] = _resolve_api_version(creds, token, creds["customer_id"])
    url = f"{API_BASE}/{creds['api_version']}/customers:listAccessibleCustomers"
    headers = {
        "Authorization": f"Bearer {str(token).strip()}",
        "developer-token": str(creds["developer_token"]).strip(),
    }
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        frappe.throw(f"Google Ads API error ({response.status_code}): {response.text}")
    names = response.json().get("resourceNames", []) or []
    return [name.split("/")[-1] for name in names]
