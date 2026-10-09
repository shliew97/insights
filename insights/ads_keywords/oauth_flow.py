# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Renew the Google Ads refresh token from inside Desk.

``oauth_helper`` (the CLI) captures Google's redirect on ``localhost:8080``,
which only works when the browser and the bench run on the same machine. This
module uses the **site itself** as the redirect target instead, so the form
button is a single click:

1. :func:`desk_authorize_url` returns Google's consent URL (``redirect_uri`` =
   this site + :data:`CALLBACK_METHOD`) and remembers a one-time ``state`` for
   the session that asked for it;
2. Google sends the browser back to :func:`callback` with ``code``/``state``;
3. the code is exchanged, the refresh token is stored on **Google Ads Settings**
   and the browser is bounced back to the form, where a flash message confirms
   the outcome.

This is the same shape as ``frappe.integrations.google_oauth.callback`` (used by
Google Drive/Contacts), plus a verifiable one-time ``state`` nonce.

The redirect URI must be registered on the OAuth client in Google Cloud, so
:func:`redirect_uri` is handed to the form and shown next to the button. If the
public URL differs from what the browser sees (reverse proxy, tunnel), pin it::

    bench --site <site> set-config google_ads_redirect_uri \
        "https://ads.example.com/api/method/insights.ads_keywords.oauth_flow.callback"
"""

from __future__ import annotations

import hmac
import http.server
import json
import os
import socket
import socketserver
import threading
import urllib.parse
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.utils import get_bench_path, get_url

from insights.ads_keywords import cache, google_ads
from insights.ads_keywords.oauth_helper import (
    ACCESS_DENIED_HELP,
    PORT,
    REDIRECT,
    authorize_url,
    exchange_code,
)

CALLBACK_METHOD = "/api/method/insights.ads_keywords.oauth_flow.callback"
START_METHOD = "/api/method/insights.ads_keywords.oauth_flow.start"
FORM_ROUTE = "/app/google-ads-settings"

# Google's authorization code is short-lived; the nonce only has to outlive the
# consent screen. The flash lives until the form picks it up after the redirect.
STATE_TTL = 15 * 60
FLASH_TTL = 5 * 60

# The loopback redirect the CLI helper already uses (http://localhost:8080/).
LOOPBACK_PORT = PORT
LOOPBACK_REDIRECT = REDIRECT


def _strip_default_port(origin: str) -> str:
    """Drop a default port from a bare origin.

    Google compares redirect URIs character for character, so ``https://host:443/x``
    and ``https://host/x`` are two different strings - and only one of them is
    registered. ``_site_address`` returns an origin only (no path), which makes
    this safe to do by suffix.
    """
    for scheme, port in (("https://", ":443"), ("http://", ":80")):
        if origin.startswith(scheme) and origin.endswith(port):
            return origin[: -len(port)]
    return origin


def _site_address() -> str:
    """Base URL of this site - works inside a request and from the console.

    ``get_url`` reads the browser's host header, so it picks up the port, tunnel
    or reverse proxy the request actually came through; that is the URI Google
    will be told about and must be registered verbatim.
    """
    try:
        return _strip_default_port(get_url(full_address=True))
    except Exception:
        # No request in flight (background job, bench console): rebuild it.
        conf = frappe.conf or {}
        protocol = "https://" if conf.get("ssl_certificate") else "http://"
        port = conf.get("webserver_port")
        origin = f"{protocol}{frappe.local.site}:{port}" if port else f"{protocol}{frappe.local.site}"
        return _strip_default_port(origin)


def redirect_uri() -> str:
    """The redirect URI given to Google, to be registered on the OAuth client."""
    override = (frappe.conf or {}).get("google_ads_redirect_uri")
    if override:
        return str(override).strip()
    return _site_address().rstrip("/") + CALLBACK_METHOD


# A message any session may consume - the CLI helper has no session of its own.
ANY_SESSION = "*"


def _state_key(user: str) -> str:
    return cache.make_key("oauth_state", user)


def _flash_key(user: str) -> str:
    return cache.make_key("oauth_flash", user)


def _flash(message: str, indicator: str = "blue", user: str | None = None) -> None:
    """Queue a message for the form the callback redirects back to."""
    cache.put(
        _flash_key(user or frappe.session.user),
        {"message": message, "indicator": indicator},
        FLASH_TTL,
    )


def flash_anywhere(message: str, indicator: str = "blue") -> None:
    """Queue a message for whichever session opens the form next.

    The CLI helper (``oauth_helper``) runs without a session, but whoever ran it
    is looking at the Desk - so confirm it there. Keep ``message`` untranslated
    (a source string): the form passes it through ``__()`` in its own language.
    """
    _flash(message, indicator, user=ANY_SESSION)


def _back_to_form() -> None:
    """Send the browser to the settings form (as frappe's own callback does)."""
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = FORM_ROUTE


def _check_google_accepts(uri: str) -> None:
    """Refuse a redirect URI Google will reject, before sending the user there.

    Google only accepts https, or plain http for localhost - anything else fails
    on Google's own error page without ever coming back to us (no ``state``, so
    no callback), which looks like a dead end.
    """
    if uri.startswith("https://"):
        return
    if (urlsplit(uri).hostname or "") in ("localhost", "127.0.0.1", "::1"):
        return

    frappe.throw(
        _(
            "Google refuses this Redirect URI: it is plain http and the host is not localhost, so "
            "the authorization would fail on Google's side. Open Desk through the site's https "
            "URL and press Get Refresh Token again (the URI shown will then be accepted), or pin "
            'the public URI with: bench --site YOUR-SITE set-config google_ads_redirect_uri '
            '"https://your-domain/api/method/insights.ads_keywords.oauth_flow.callback"'
        )
    )


def _google_console_url(client_id: str | None) -> str:
    """Deep link to the credentials page of the client's Google Cloud project.

    An OAuth client id starts with the project *number* (``139374577103-...``),
    which the console accepts as ``?project=`` - so the link lands on the very
    client this app uses instead of an arbitrary project.
    """
    base = "https://console.cloud.google.com/apis/credentials"
    project = str(client_id or "").split("-", 1)[0]
    return f"{base}?project={project}" if project.isdigit() else base


@frappe.whitelist()
def desk_authorize_url() -> dict:
    """Google consent URL for the form's **Get Refresh Token** button."""
    creds = google_ads.get_credentials()
    if not (creds.get("client_id") and creds.get("client_secret")):
        frappe.throw(_("Save the Client ID and Client Secret first, then try again."))

    uri = redirect_uri()
    _check_google_accepts(uri)
    state = {
        "user": frappe.session.user,
        "redirect_uri": uri,
        "nonce": frappe.generate_hash(length=32),
    }
    cache.put(_state_key(frappe.session.user), state, STATE_TTL)

    return {
        "url": authorize_url(creds["client_id"], uri, json.dumps(state)),
        "redirect_uri": uri,
        "start_url": get_url(START_METHOD),
        "credentials_url": _google_console_url(creds.get("client_id")),
        "loopback_redirect": LOOPBACK_REDIRECT,
        "port": LOOPBACK_PORT,
    }


@frappe.whitelist(methods=["GET"])
def start() -> None:
    """Bookmarkable entry point: 302 the browser straight to Google.

    ``GET /api/method/insights.ads_keywords.oauth_flow.start`` runs the same first
    half of the flow as the **Get Refresh Token** button, so renewing never
    depends on the Desk form JavaScript being fresh - and the URL can be
    bookmarked next to the Ads reports.
    """
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = desk_authorize_url()["url"]


def _consume_state(user: str, nonce: str) -> bool:
    """Single-use check of the nonce issued to ``user``.

    A code (or a whole ``state``) replayed into another session is rejected, and
    so is a second delivery of the same one.
    """
    expected = cache.get(_state_key(user)) or {}
    if not nonce or not hmac.compare_digest(str(expected.get("nonce") or ""), nonce):
        return False

    try:
        frappe.cache().delete_value(_state_key(user))
    except Exception:
        pass
    return True


def _complete(state: dict, user: str, code: str | None, error: str | None) -> dict:
    """Verify the state, swap the code for a token, store it, queue a flash.

    The shared tail of both flows (form callback and loopback listener) so they
    behave - and get verified - identically. Returns ``{"ok", "message",
    "indicator"}``; the caller decides how to get the browser back.
    """
    # Consume the state first: a delivery that is refused (or an error coming
    # back from Google) must still burn the nonce, otherwise the same link could
    # be replayed later.
    if not _consume_state(user, str(state.get("nonce") or "")):
        message = _(
            "Refresh Token unchanged: this authorization link was already used or was "
            "started in a different session. Click Get Refresh Token and try again."
        )
        _flash(message, "red", user=user)
        return {"ok": False, "message": message, "indicator": "red"}

    if error:
        message = _("Google refused the authorization ({0}).").format(frappe.bold(error))
        if error == "access_denied":
            message = f"{message}<br>{ACCESS_DENIED_HELP.replace(chr(10), '<br>')}"
        _flash(message, "red", user=user)
        return {"ok": False, "message": message, "indicator": "red"}

    if not code:
        message = _("Google did not return an authorization code. Please try again.")
        _flash(message, "red", user=user)
        return {"ok": False, "message": message, "indicator": "red"}

    creds = google_ads.get_credentials()
    try:
        payload = exchange_code(
            creds["client_id"],
            creds["client_secret"],
            code,
            str(state.get("redirect_uri") or redirect_uri()),
        )
    except Exception as failure:
        message = _("Token exchange failed: {0}").format(frappe.bold(str(failure)[:300]))
        _flash(message, "red", user=user)
        return {"ok": False, "message": message, "indicator": "red"}

    token = payload.get("refresh_token")
    if not token:
        message = _(
            "Google returned no refresh token. Remove this app's access at "
            "https://myaccount.google.com/permissions and try again."
        )
        _flash(message, "red", user=user)
        return {"ok": False, "message": message, "indicator": "red"}

    try:
        google_ads.save_refresh_token(token)
    except Exception as failure:
        message = _("Could not save the Refresh Token: {0}").format(frappe.bold(str(failure)[:300]))
        _flash(message, "red", user=user)
        return {"ok": False, "message": message, "indicator": "red"}

    message = _("Refresh Token updated successfully.")
    _flash(message, "green", user=user)
    return {"ok": True, "message": message, "indicator": "green"}


@frappe.whitelist()
def consume_flash() -> dict | None:
    """Pop the message left for this user (or a site-wide one) - read by the form."""
    for key in (_flash_key(frappe.session.user), _flash_key(ANY_SESSION)):
        payload = cache.get(key)
        if payload:
            try:
                frappe.cache().delete_value(key)
            except Exception:
                pass
            return payload
    return None


@frappe.whitelist(methods=["GET"])
def callback(state: str | None = None, code: str | None = None, error: str | None = None) -> None:
    """Google redirects the browser here: ``?code&state`` or ``?error&state``."""
    if not state:
        # Opened by hand - the dialog shows this URL as the *Redirect URI* to
        # register, so explain instead of implying a replay.
        _flash(
            _(
                "This is the OAuth callback URL. It only works as the redirect target of the "
                "Google consent screen, not when opened directly - open Google Ads Settings and "
                "click Get Refresh Token."
            ),
            "orange",
        )
        return _back_to_form()

    try:
        parsed = json.loads(state)
    except Exception:
        parsed = {}

    if parsed.get("user") != frappe.session.user:
        _flash(
            _(
                "Refresh Token unchanged: this authorization link was already used or was "
                "started in a different session. Click Get Refresh Token and try again."
            ),
            "red",
        )
        return _back_to_form()

    _complete(parsed, frappe.session.user, code, error)
    return _back_to_form()


# ---------------------------------------------------------------------------
# Loopback flow: capture the callback on localhost, exactly like the CLI helper
# ---------------------------------------------------------------------------
# Google compares redirect URIs character for character, so the form callback
# above must be registered on the OAuth client first. Native/desktop apps dodge
# that with a *loopback* redirect (RFC 8252): the client listens on
# ``http://localhost:<port>/`` itself - which is what ``oauth_helper`` does when
# run by hand. This section brings the same trick into the Desk: the bench starts
# the listener itself, so the token still lands on Google Ads Settings and the
# browser is sent back to the form it came from. No Google Cloud change is needed
# as long as ``http://localhost:<port>/`` is authorised on the client (it is,
# whenever the CLI helper has ever worked for this client).

_LISTENER_LOCK = threading.Lock()
_LISTENER: dict = {"server": None}


class _LoopbackHandler(http.server.BaseHTTPRequestHandler):
    """Answers the browser Google sent to ``http://localhost:<port>/``."""

    server_version = "ads-keywords-oauth"

    def do_GET(self):  # noqa: N802 (http.server API)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (query.get("code") or [None])[0]
        error = (query.get("error") or [None])[0]
        state = (query.get("state") or [None])[0]

        if not code and not error:
            # Someone opened the port directly - nothing to do here. Plain English
            # on purpose: this thread has no frappe context to translate with.
            self._reply(
                _loopback_page(
                    "Waiting for Google",
                    "<p>This port is reserved for the Google Ads authorization.</p>"
                    "<p>Open Google Ads Settings and click <b>Get Refresh Token</b>.</p>",
                )
            )
            return

        try:
            outcome = finish_local_flow(state, code, error)
        except Exception as failure:
            # Never leave the browser on a dead connection.
            outcome = {
                "ok": False,
                "title": "Authorization failed",
                "message": f"{type(failure).__name__}: {failure}",
                "desk_url": "",
            }
        colour = "#0a7d33" if outcome["ok"] else "#b93a32"
        self._reply(
            _loopback_page(
                outcome.get("title") or "Google Ads authorization",
                f'<p style="color:{colour}">{outcome.get("message") or ""}</p>',
                outcome.get("desk_url"),
            )
        )

    def _reply(self, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # keep the console quiet
        pass


def _loopback_page(title: str, body: str, desk_url: str | None = None) -> str:
    """Tiny self-contained landing page (with a link back to the form)."""
    back = ""
    if desk_url:
        # Both values are escaped: the URL is built from the request host.
        href = urllib.parse.quote(desk_url, safe=":/?#[]@!$&'*+,;=%~-")
        back = (
            f'<p><a href="{href}">{href}</a></p>'
            f"<script>setTimeout(function () {{ location.href = {json.dumps(desk_url)}; }}, 1500);</script>"
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title>"
        "<style>body{font:15px/1.6 system-ui,sans-serif;margin:12vh auto;max-width:42rem;"
        "padding:0 1.5rem;color:#1f272e}code{background:#f4f5f7;padding:.1rem .3rem;border-radius:4px}"
        "</style></head><body>"
        f"<h2>{title}</h2>{body}{back}</body></html>"
    )


def _sites_path() -> str:
    """The bench's sites dir - the listener thread has no frappe context yet."""
    return os.environ.get("SITES_PATH") or os.path.join(get_bench_path(), "sites")


def _listener_alive() -> bool:
    """True when something already answers the loopback port.

    That can be our own listener (started by another worker) or a CLI helper left
    running in a terminal - either way the callback will be captured.
    """
    try:
        with socket.create_connection(("127.0.0.1", LOOPBACK_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _ensure_loopback_listener() -> str:
    """Start the loopback server in this process when nothing serves the port."""
    if _LISTENER["server"] is not None or _listener_alive():
        return "running"

    with _LISTENER_LOCK:
        if _LISTENER["server"] is not None or _listener_alive():
            return "running"

        socketserver.TCPServer.allow_reuse_address = True
        try:
            server = socketserver.ThreadingTCPServer(("127.0.0.1", LOOPBACK_PORT), _LoopbackHandler)
        except OSError as failure:
            frappe.throw(
                _(
                    "Something already holds port {0} ({1}), so the localhost callback cannot "
                    "start. Close it (a leftover oauth_helper run?) and try again, or use the "
                    "site callback instead."
                ).format(LOOPBACK_PORT, failure)
            )

        server.daemon_threads = True
        threading.Thread(
            target=server.serve_forever,
            name="ads-keywords-oauth-loopback",
            daemon=True,
        ).start()
        _LISTENER["server"] = server
        return "started"


@frappe.whitelist()
def local_authorize_url() -> dict:
    """Consent URL that comes back to the loopback port - no Google Cloud change.

    The Desk version of what ``oauth_helper`` does: it reuses the redirect URI
    that is already authorised for this client (``http://localhost:8080/``), the
    bench answers that port itself, and the browser is returned to the form it
    came from.
    """
    creds = google_ads.get_credentials()
    if not (creds.get("client_id") and creds.get("client_secret")):
        frappe.throw(_("Save the Client ID and Client Secret first, then try again."))

    listener = _ensure_loopback_listener()
    state = {
        "user": frappe.session.user,
        "site": frappe.local.site,
        "redirect_uri": LOOPBACK_REDIRECT,
        "desk_url": get_url(FORM_ROUTE),
        "nonce": frappe.generate_hash(length=32),
    }
    cache.put(_state_key(frappe.session.user), state, STATE_TTL)

    return {
        "url": authorize_url(creds["client_id"], LOOPBACK_REDIRECT, json.dumps(state)),
        "redirect_uri": LOOPBACK_REDIRECT,
        "port": LOOPBACK_PORT,
        "listener": listener,
    }


def finish_local_flow(state_raw: str | None, code: str | None, error: str | None) -> dict:
    """Handle the loopback hit and describe the outcome to the browser.

    Runs in the listener thread, which is not a frappe request: it needs its own
    ``init``/``connect`` (the same dance the CLI helper does), and the site name
    has to travel in the state because there is no request host to resolve it.
    """
    try:
        state = json.loads(state_raw or "{}")
    except Exception:
        state = {}

    site = str(state.get("site") or "")
    user = str(state.get("user") or "")
    desk_url = str(state.get("desk_url") or "")

    if not (site and user):
        return {
            "ok": False,
            "title": "Authorization failed",
            "message": "This callback carries no state - start again from Google Ads Settings.",
            "desk_url": desk_url,
        }

    frappe.init(site=site, sites_path=_sites_path())
    try:
        frappe.connect()
        frappe.set_user(user)
        outcome = _complete(state, user, code, error)
        outcome["title"] = _("Refresh Token updated") if outcome["ok"] else _("Authorization failed")
        outcome["desk_url"] = desk_url
        frappe.db.commit()
        return outcome
    except Exception as failure:
        try:
            frappe.log_error(
                title="Google Ads loopback callback failed",
                message=frappe.get_traceback(),
            )
        except Exception:
            pass
        return {
            "ok": False,
            "title": "Authorization failed",
            "message": f"{type(failure).__name__}: {failure}",
            "desk_url": desk_url,
        }
    finally:
        try:
            frappe.destroy()
        except Exception:
            pass
