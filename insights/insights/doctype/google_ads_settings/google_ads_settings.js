// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

const GOOGLE_ADS_SETTINGS_METHOD = "insights.insights.doctype.google_ads_settings.google_ads_settings";

frappe.ui.form.on("Google Ads Settings", {
	refresh(frm) {
		show_refresh_token_message();

		// One-click refresh-token renewal. Google redirects the browser to
		// /api/method/insights.ads_keywords.oauth_flow.callback, which saves the
		// token on this form and bounces straight back here.
		frm.add_custom_button(__("Get Refresh Token"), () => get_refresh_token());

		// Use frappe's built-in freeze so the overlay is always removed
		// (frappe.request.cleanup unfreezes in its `.always` handler).
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "insights.insights.doctype.google_ads_settings.google_ads_settings.test_connection",
				freeze: true,
				freeze_message: __("Connecting to Google Ads..."),
				callback: (response) => {
					const result = response.message || {};
					if (result.ok) {
						frappe.show_alert({
							message: __(result.message || "Connected."),
							indicator: "green",
						});
					} else {
						frappe.msgprint({
							title: __("Connection failed"),
							message: __(result.message || "Unknown error"),
							indicator: "red",
						});
					}
				},
				error: () => {
					frappe.msgprint({
						title: __("Connection failed"),
						message: __("Could not reach the server. Please try again."),
						indicator: "red",
					});
				},
			});
		});

		frm.add_custom_button(__("List Accounts"), () => {
			frappe.call({
				method: "insights.insights.doctype.google_ads_settings.google_ads_settings.list_accounts",
				freeze: true,
				freeze_message: __("Listing Google Ads accounts..."),
				callback: (response) => {
					const result = response.message || {};
					frappe.msgprint({
						title: __("Google Ads accounts"),
						message: (result.message || "").replace(/\n/g, "<br>"),
						indicator: result.ok ? "blue" : "red",
					});
				},
				error: () => {
					frappe.msgprint({
						title: __("Failed"),
						message: __("Could not reach the server. Please try again."),
						indicator: "red",
					});
				},
			});
		});
	},
});

function show_refresh_token_message() {
	// Left behind by the OAuth callback (or the CLI helper) for the form to show.
	frappe.call({
		method: `${GOOGLE_ADS_SETTINGS_METHOD}.consume_refresh_token_message`,
		callback: ({ message }) => {
			if (!message || !message.message) return;
			frappe.show_alert(
				{
					// Server messages arrive either already translated or as a source
					// string; __() handles both (an unknown key returns the input).
					message: __(message.message),
					indicator: message.indicator || "blue",
				},
				15
			);
		},
	});
}

// The same flow without this button - also the escape hatch when anything below
// fails, so a broken page never leaves the user at a dead end.
function start_url_fallback() {
	return `${window.location.origin}/api/method/insights.ads_keywords.oauth_flow.start`;
}

function report_flow_problem(title, detail, direct_url) {
	frappe.msgprint({
		title: title,
		indicator: "red",
		message: `
			<p>${__(
				"Reload the page with Ctrl+Shift+R and try again, or open this URL directly - it starts the very same flow:"
			)}</p>
			<p><code>${frappe.utils.escape_html(direct_url || start_url_fallback())}</code></p>
			<pre class="small">${frappe.utils.escape_html(detail == null ? "" : String(detail))}</pre>`,
	});
}

// frappe wraps server-side messages in a JSON string inside a JSON string; pull
// the text out so a frappe.throw() reaches the user readable.
function response_problem(response) {
	if (response && response._server_messages) {
		try {
			return JSON.parse(response._server_messages)
				.map((entry) => JSON.parse(entry).message)
				.join("\n");
		} catch (error) {
			return response._server_messages;
		}
	}
	return (response && response.exception) || JSON.stringify(response);
}

function get_refresh_token() {
	frappe.call({
		method: `${GOOGLE_ADS_SETTINGS_METHOD}.get_refresh_token_url`,
		freeze: true,
		freeze_message: __("Preparing Google authorization..."),
		callback: (response) => {
			const result = response.message;
			if (result && result.url) {
				show_authorize_dialog(result);
				return;
			}
			// Never go quiet here: returning silently is what turns a server error
			// (or a stale page) into a button that appears to do nothing.
			report_flow_problem(
				__("Could not start the Google authorization"),
				response_problem(response)
			);
		},
		error: () => {
			frappe.msgprint({
				title: __("Could not reach the server"),
				indicator: "red",
				message: __("Please reload the page with Ctrl+Shift+R and try again."),
			});
		},
	});
}

function show_authorize_dialog({
	url,
	redirect_uri,
	start_url,
	credentials_url,
	loopback_redirect,
	port,
}) {
	// Deep link to *your* project's credentials page (project number = Client ID prefix).
	const console_url = credentials_url || "https://console.cloud.google.com/apis/credentials";
	const audience_url = "https://console.cloud.google.com/auth/audience";
	const loopback = loopback_redirect || `http://localhost:${port || 8080}/`;
	// The same flow without this button - worth bookmarking next to the Ads reports.
	const start_html = start_url
		? `<p class="text-muted">${__(
				"Tip: this URL runs the same flow and can be bookmarked"
			)}: <a href="${frappe.utils.escape_html(start_url)}"><code>${frappe.utils.escape_html(
				start_url
			)}</code></a></p>`
		: "";
	const html = `
		<div style="line-height: 1.7">
			<p><b>${__("Way 1 - via localhost, nothing to register")}</b><br>
			${__(
				"Same trick as the bundled CLI helper: Google sends the browser back to {0}, this app answers that port itself, saves the token and returns you here. Use 'Authorize via localhost' below - it only needs the VS Code port forwarding for that port.",
				[`<code>${frappe.utils.escape_html(loopback)}</code>`]
			)}</p>
			<hr>
			<p><b>${__("Way 2 - via this site's callback (register it once)")}</b></p>
			<ol class="pl-3">
				<li>${__(
					"Add this exact <b>Redirect URI</b> to the OAuth client's <i>Authorised redirect URIs</i>"
				)}
				(<a href="${console_url}" target="_blank">${__("Google Cloud credentials")}</a>):
				<br><code>${frappe.utils.escape_html(redirect_uri)}</code>
				<button class="btn btn-xs btn-default ml-2" id="ads-keywords-copy-uri">${__(
					"Copy Redirect URI"
				)}</button>
				<br><small class="text-muted">${__(
					"It must match character for character - keep the https:// prefix, add no trailing slash, and edit the OAuth client whose Client ID is set above. Google takes a minute or two to pick up a change."
				)}</small></li>
				<li>${__("Continue with the Google account that can access the Ads account.")}</li>
				<li>${__(
					"You come back to this form and the new Refresh Token is saved automatically."
				)}</li>
			</ol>
			${start_html}
			<p><b>${__("Long-lived token")}:</b> ${__(
				"Google expires refresh tokens after 7 days while the OAuth consent screen stays in <b>Testing</b>. On {0} publish the app, or set the user type to <b>Internal</b> for a Google Workspace domain, then run this once more.",
				[`<a href="${audience_url}" target="_blank">${__("Google Auth Platform")}</a>`]
			)}</p>
		</div>`;

	try {
		// frappe.ui.Dialog is an ES class: it must be called with `new`.
		const dialog = new frappe.ui.Dialog({
			title: __("Get Refresh Token"),
			fields: [{ fieldtype: "HTML", options: html }],
			primary_action_label: __("Authorize with Google"),
			primary_action() {
				// Same tab on purpose: Google redirects straight back to this form.
				window.location.href = url;
			},
			secondary_action_label: __("Authorize via localhost"),
			secondary_action() {
				start_local_flow(dialog);
			},
		});
		dialog.show();
		dialog.$wrapper.find("#ads-keywords-copy-uri").on("click", () => {
			frappe.utils.copy_to_clipboard(redirect_uri);
		});
	} catch (error) {
		report_flow_problem(
			__("Could not open the authorization dialog"),
			(error && error.stack) || error,
			start_url
		);
	}
}

function start_local_flow(dialog) {
	frappe.call({
		method: `${GOOGLE_ADS_SETTINGS_METHOD}.get_local_refresh_token_url`,
		freeze: true,
		freeze_message: __("Preparing Google authorization..."),
		callback: (response) => {
			const result = response.message;
			if (result && result.url) {
				if (dialog) dialog.hide();
				// Same tab: Google returns to http://localhost:<port>/, which this
				// app answers itself and then bounces back to this form.
				window.location.href = result.url;
				return;
			}
			report_flow_problem(
				__("Could not start the Google authorization"),
				response_problem(response)
			);
		},
		error: () => {
			frappe.msgprint({
				title: __("Could not reach the server"),
				indicator: "red",
				message: __("Please reload the page with Ctrl+Shift+R and try again."),
			});
		},
	});
}


