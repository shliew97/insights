# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Mint a Google Ads OAuth2 refresh token.

The **Client ID and Client Secret are read from the Google Ads Settings form**
(site config is used as a fallback), so there is nothing to type. The script
prints an authorisation URL, captures the redirect on localhost, exchanges the
code and **saves the refresh token back into Google Ads Settings**::

    cd /home/frappe/frappe-bench
    python -m insights.ads_keywords.oauth_helper --site <site>

Prerequisites
-------------
* Client ID / Client Secret filled in on the **Google Ads Settings** form;
* the redirect URI ``http://localhost:8080/`` added to that client's
  *Authorised redirect URIs* (set ``OAUTH_LOCAL_PORT`` if 8080 is taken);
* the Google Ads API enabled on the Google Cloud project.

Note: while the OAuth consent screen is in **Testing** status Google expires
refresh tokens after 7 days. Set the app's user type to **Internal** (Google
Workspace) or publish it to get a long-lived token.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import urllib.parse
import webbrowser

import requests

SCOPE = "https://www.googleapis.com/auth/adwords"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

PORT = int(os.environ.get("OAUTH_LOCAL_PORT", "8080"))
REDIRECT = f"http://localhost:{PORT}/"

_captured: dict[str, str | None] = {"code": None, "error": None}

ACCESS_DENIED_HELP = """
Google blocked the sign-in with 'access_denied'. This is almost always an OAuth
consent-screen setting, not a script problem. In the new "Google Auth Platform"
UI open https://console.cloud.google.com/auth/audience for the client's project:
  * Publishing status = "Testing"  ->  add the exact Google account you sign in
    with under "Test users" (Add users -> enter the email -> Save).
  * User type = "Internal"  ->  only accounts in that Google Workspace org may
    sign in; switch to "External" (or use an org account).
  * Make sure "Branding" (app name, user support email) is filled in.
Also make sure the Google Ads API is enabled for the project.
Then run this script again.
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "error" in params:
            _captured["error"] = params["error"][0]
        _captured["code"] = (params.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Done - you can close this tab and return to the terminal.</h2>")

    def log_message(self, *args):  # keep the console clean
        pass


def authorize_url(client_id: str, redirect_uri: str = REDIRECT, state: str | None = None) -> str:
    """Google consent URL.

    ``access_type=offline`` + ``prompt=consent`` are what make Google hand back a
    refresh token on every run; ``state`` is echoed back to ``redirect_uri``.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str = REDIRECT
) -> dict:
    """Swap an authorization code for tokens. Raises ``RuntimeError`` on failure.

    ``redirect_uri`` must be byte-identical to the one the code was issued for.
    """
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {response.status_code} {response.text}")
    return response.json()


def _detect_site() -> str | None:
    """Best-effort site detection from the bench ``sites`` directory."""
    sites_path = os.environ.get("SITES_PATH", "sites")

    current = os.path.join(sites_path, "currentsite.txt")
    if os.path.exists(current):
        with open(current) as handle:
            name = handle.read().strip()
        if name:
            return name

    common = os.path.join(sites_path, "common_site_config.json")
    if os.path.exists(common):
        try:
            with open(common) as handle:
                default_site = json.load(handle).get("default_site")
            if default_site:
                return default_site
        except Exception:
            pass

    try:
        # A site "alias" (e.g. a tunnel hostname symlinked to the real site dir)
        # must not count as a second site: dedupe by real path, preferring the
        # real directory over any symlink pointing at it.
        entries = []
        for name in os.listdir(sites_path):
            path = os.path.join(sites_path, name)
            if os.path.exists(os.path.join(path, "site_config.json")):
                entries.append((os.path.islink(path), os.path.realpath(path), name))
    except Exception:
        entries = []

    entries.sort(key=lambda entry: entry[0])  # real directories first
    distinct: dict[str, str] = {}
    for _is_link, real_path, name in entries:
        distinct.setdefault(real_path, name)

    names = list(distinct.values())
    return names[0] if len(names) == 1 else None


def _init_frappe(site: str) -> None:
    import frappe

    frappe.init(site=site, sites_path=os.environ.get("SITES_PATH", "sites"))
    frappe.connect()
    frappe.set_user("Administrator")


def _credentials_from_settings(site: str) -> tuple[str | None, str | None]:
    """Read Client ID / Client Secret from Google Ads Settings (site config fallback)."""
    try:
        _init_frappe(site)
        from insights.ads_keywords import google_ads

        creds = google_ads.get_credentials()
        client_id, client_secret = creds.get("client_id"), creds.get("client_secret")
        if client_id and client_secret:
            print(f"Using Client ID / Client Secret from Google Ads Settings ({site}).")
        return client_id, client_secret
    except Exception as error:
        print(f"Could not read Google Ads Settings: {error}")
        return None, None


def _save_refresh_token(token: str) -> bool:
    """Write the refresh token back into Google Ads Settings."""
    try:
        from insights.ads_keywords import google_ads, oauth_flow

        google_ads.save_refresh_token(token)
        # The Desk is usually still open while this runs - confirm it there too.
        # Kept as a source string so the form can translate it (see flash_anywhere).
        oauth_flow.flash_anywhere("Refresh Token updated via the command line helper.")
        return True
    except Exception as error:
        print(f"Could not save into Google Ads Settings: {error}")
        return False


def _exchange(client_id: str, client_secret: str, code: str) -> str | None:
    try:
        payload = exchange_code(client_id, client_secret, code)
    except RuntimeError as error:
        print(error)
        return None

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        print(
            "\nNo refresh_token returned. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run the script again."
        )
        return None
    return refresh_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a Google Ads refresh token.")
    parser.add_argument("--site", help="Site to read Google Ads Settings from")
    args = parser.parse_args()

    site = args.site or os.environ.get("FRAPPE_SITE") or _detect_site()
    client_id = client_secret = None
    if site:
        client_id, client_secret = _credentials_from_settings(site)
    else:
        print("No site detected - pass --site <site> to read Google Ads Settings.")

    if not client_id:
        client_id = input("Client ID: ").strip()
    if not client_secret:
        client_secret = input("Client Secret: ").strip()

    print(f"\nRegister this EXACT redirect URI on your OAuth client first:\n  {REDIRECT}\n")
    url = authorize_url(client_id)
    print(f"1. Open this URL and approve access:\n\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("localhost", PORT), _Handler) as httpd:
        print(f"2. Waiting for the redirect on {REDIRECT} (Ctrl-C to abort)...")
        httpd.handle_request()

    if _captured["error"]:
        print("Authorization failed:", _captured["error"])
        if _captured["error"] == "access_denied":
            print(ACCESS_DENIED_HELP)
        return
    code = _captured["code"]
    if not code:
        print("No authorization code received.")
        return

    refresh_token = _exchange(client_id, client_secret, code)
    if not refresh_token:
        return

    print("\nRefresh token:\n")
    print(refresh_token)
    if site and _save_refresh_token(refresh_token):
        print("\nSaved into Google Ads Settings -> Refresh Token.")
    else:
        print("\nCopy it into Google Ads Settings -> Refresh Token.")


if __name__ == "__main__":
    main()
