# Google Ads keyword analysis

Classifies Google Ads search keywords into an *intent* so the marketing team can
find the high-intent terms worth bidding on and the irrelevant / negative terms
that waste spend. It sits under the **Customer Retention** area of **Insights**.

```
"massage near me"      -> HIGH_INTENT
"deep tissue massage"  -> HIGH_INTENT
"massage course"       -> IRRELEVANT
"massage chair"        -> POTENTIAL_NEGATIVE
```

## Intent taxonomy

| Intent | Meaning | Suggested action |
| --- | --- | --- |
| `HIGH_INTENT` | searcher is ready to book / buy the service | keep / increase bid |
| `IRRELEVANT` | intent mismatch (jobs, courses, definitions, DIY) | pause keyword |
| `POTENTIAL_NEGATIVE` | likely wasted spend (products, freebies, off-brand) | add as negative keyword |
| `UNKNOWN` | engine unsure -> listed in the review table to be codified | manual review |

## Layout

| File | Responsibility |
| --- | --- |
| `config.py` | Intent taxonomy and every tunable signal term. |
| `engine.py` | Pure, rule-based classifier (no `frappe`, fully unit-tested). |
| `google_ads.py` | Google Ads API adapter (REST) - fetches the real keywords. |
| `oauth_flow.py` | One-click refresh-token renewal from Desk (site callback). |
| `oauth_helper.py` | CLI fallback that captures the redirect on `localhost:8080`. |
| `cache.py` | Redis cache for the two quota-limited API steps (quota/cost). |
| `sources.py` | Parses explicitly passed keyword input (list / CSV). |
| `backend_functions.py` | Whitelisted `Insights Query v3` backend functions. |
| `setup.py` | Seeds the workbook, charts and dashboard. |
| `tests/test_engine.py` | Unit tests for the engine and the parser. |

## Running the tests

```bash
cd apps/insights
python -m unittest insights.ads_keywords.tests.test_engine
```

## Filling in the Google Ads API credentials

All credentials are entered in one place: the **Google Ads Settings** single
doctype. In the Desk, search for **Google Ads Settings** (or open
`/app/google-ads-settings`), fill in the fields and save. Use the **Test
Connection** button to verify.

| Field | Where to get it |
| --- | --- |
| Developer Token | Google Ads manager account -> Tools & Settings -> API Center |
| Client ID / Client Secret | Google Cloud Console -> APIs & Services -> Credentials (OAuth client); enable the Google Ads API on the project |
| Refresh Token | OAuth2 refresh token for the scope `https://www.googleapis.com/auth/adwords`. Leave it empty and press **Get Refresh Token** to obtain one |
| Login Customer ID (MCC) | The manager account id, only if you access the account through one |
| Customer ID | The Google Ads account id to analyse (dashes are fine) |

As an alternative (or fallback) you can set the same values on the site config -
the Settings form always takes precedence, and the config is only used for fields
left blank on the form:

```bash
bench --site <site> set-config google_ads_developer_token "..."
bench --site <site> set-config google_ads_client_id "..."
bench --site <site> set-config google_ads_client_secret "..."
bench --site <site> set-config google_ads_refresh_token "..."
bench --site <site> set-config google_ads_login_customer_id "1234567890"
bench --site <site> set-config google_ads_customer_id "1234567890"
```

To mint a refresh token, generate an authorization URL for the
`https://www.googleapis.com/auth/adwords` scope with your client id, approve it,
then exchange the returned `code` at `https://oauth2.googleapis.com/token`
(`grant_type=authorization_code`).

### Getting / Renewing the Refresh Token

Three options, all for the scope `https://www.googleapis.com/auth/adwords`.
Renewing is routine: keep the one you find least hassle.

1. **From Desk, one click.** Open *Google Ads Settings* and use the **Get Refresh
   Token** button. It shows the *Redirect URI* to register on the OAuth client
   (Google Cloud -> APIs & Services -> Credentials -> *Authorised redirect URIs*),
   then sends the browser to Google. Google redirects back to
   `/api/method/insights.ads_keywords.oauth_flow.callback`, which exchanges the
   code, saves the token on the form and returns you to it with a confirmation -
   no terminal and no copy/paste. The callback is only accepted for the session
   that started it (one-time `state` nonce in Redis). If the public URL differs
   from what the browser sees (reverse proxy, tunnel), pin the URI:

   ```bash
   bench --site <site> set-config google_ads_redirect_uri "https://ads.example.com/api/method/insights.ads_keywords.oauth_flow.callback"
   ```

   The same flow is available **without any Desk JavaScript** (handy when a stale
   browser cache leaves the button unresponsive) - bookmark this URL and it
   302s straight to Google:

   `<site>/api/method/insights.ads_keywords.oauth_flow.start`

   **No Google Cloud change at all?** Use *Authorize via localhost* in the same
   dialog. That is the loopback flow (RFC 8252) the CLI helper already uses:
   Google is handed `http://localhost:8080/` - a redirect URI this client has
   almost certainly authorised already, because `oauth_helper` has worked with it
   - and the *bench itself* answers that port, stores the token and sends the
   browser back to the form. It only needs port 8080 reachable from the browser
   (VS Code forwards it in a devcontainer). `oauth_flow.py` starts the listener
   on demand and reports it in the dialog; if something else already holds the
   port (a leftover `oauth_helper` run), it says so instead of failing silently.

   > Google accepts an **https** redirect URI only (plain http is allowed for
   > `localhost` / `127.0.0.1`). The button therefore refuses to start a flow
   > that is certain to fail - open Desk over https instead of
   > `http://healthland.localhost:8000`, or pin the public URI as shown above.
   > The callback URL is not meant to be opened by hand: it only answers the
   > redirect coming back from Google.

2. **Bundled CLI helper**, for when the site has no URL you can register with
   Google: it captures the redirect on `localhost:8080`. It reads the Client ID
   and Client Secret straight from the **Google Ads Settings** form (site config
   is the fallback) and writes the refresh token back into that form:

   ```bash
   cd /home/frappe/frappe-bench
   python -m insights.ads_keywords.oauth_helper --site <site>
   ```

   Fill in Client ID / Client Secret on the form first, add
   `http://localhost:8080/` to the OAuth client's authorised redirect URIs, and
   sign in with a Google account that can access the Ads account. The site is
   auto-detected from `sites/currentsite.txt` / `sites/common_site_config.json`;
   pass `--site` or set `FRAPPE_SITE` to override, and `OAUTH_LOCAL_PORT` to use
   another port.

3. **OAuth 2.0 Playground** (no local server): open
   <https://developers.google.com/oauthplayground>, click the gear, tick *Use
   your own OAuth credentials* and enter the Client ID/Secret; in step 1 enter
   the scope `https://www.googleapis.com/auth/adwords`, authorise, then in step 2
   copy the **Refresh token**. Add
   `https://developers.google.com/oauthplayground` to the client's authorised
   redirect URIs.

### Making the token long-lived ("permanent")

A refresh token does **not** expire on its own - the 7-day expiry is a property
of the OAuth app, not of the token: while the consent screen sits in **Testing**
status Google forces every refresh token to die after 7 days. Renewing is a
chore, so make the app permanent instead:

1. open <https://console.cloud.google.com/auth/audience> for the client's project,
2. **Publish app** (any Google account may then authorise), or set **User type**
   to **Internal** (only accounts in your Google Workspace domain),
3. open *Google Ads Settings* and click **Get Refresh Token** once more.

The token minted after that step never expires, so the button becomes an
emergency tool rather than a weekly routine. Re-authorizing without publishing
the app does not help - the consent screen status is the only thing that counts.

### Language / translations

`insights/translations/zh.csv` translates the **Google Ads Settings** form, its
buttons and every message of the token flow into Simplified Chinese. Frappe reads
each installed app's CSV at runtime (no build step), but translations only apply
when the language is not English:

* whole site: **System Settings -> Language -> 简体中文**, or
* just your login: **User -> Language -> 简体中文**.

Added strings appear after a page reload; the merged dictionary is cached in
Redis (`merged_translations`), which `bench --site <site> clear-cache` refreshes.

If Google returns **403 access_denied**, your account is not allowed by the OAuth
consent screen. In the new *Google Auth Platform* console open
<https://console.cloud.google.com/auth/audience> for the client's project and
either add the signing-in account under **Test users** (publishing status
*Testing*) or set the **User type** to *External*/*Internal* as appropriate.
Make sure **Branding** (app name, support email) is filled in.

### Customer ID vs Login Customer ID (MCC)

* **Customer ID** - the Google Ads account whose keywords you want to analyse
  (top bar of the Google Ads UI, e.g. `123-456-7890`).
* **Login Customer ID (MCC)** - the manager account id used to reach that
  account. Set it whenever your access goes through a manager (MCC) account;
  leave it blank only if you log in directly into the target account.

#### If your account is itself a manager (MCC)

A manager account has no campaigns or keywords of its own, so put the
**manager's id in Customer ID** (and the same manager id in *Login Customer
ID*). The adapter detects that it is a manager and **pulls the keywords of all
its enabled child advertiser accounts**, merging them into one row per unique
keyword (see the `Accounts` column). To analyse a single advertiser account
instead, put that child account id in **Customer ID** and keep the manager id in
*Login Customer ID*. Pass `"expand_manager": false` in the query args to turn the
expansion off.

`Test Connection` reports how many advertiser accounts were found under the
manager.

### API version

The form's **API Version** defaults to the current GA version (`v23`). Google
retires API versions roughly once a year (v19-v21 are already gone); if the
configured version is retired the app probes nearby versions and automatically
uses a working one, so you normally never have to touch this field.

## Why there is no LLM in the classification path

An earlier revision could hand the keywords the rules were unsure about to a
local LLaMA/Ollama model for a "second opinion" (`llm.py`, `use_llm`,
`llm_confidence_threshold`). It was **removed on purpose**:

1. **Intent here is policy, not language understanding.** Whether `massage
   course` is a customer is decided by what *this* business sells and refuses to
   bid on - something no general model knows. Telling it means handing the rule
   list back to it in prose, so a model only adds a lossy, non-deterministic
   indirection over information you already have.
2. **The cascade feeds it the worst cases.** The rules are confident about
   exactly the strings a model finds easy (`course`, `job`, `chair`, `spa`) and
   defer only the residue: business-specific vocabulary, misspellings, Malay /
   Chinese variants, brand names. That is where a general model is weakest - and
   where it answers fluently anyway.
3. **It cannot see the evidence that matters.** Impressions, cost, conversions
   and the owner's existing negative keywords decide a keyword's value; a model
   sees only the string.
4. **Ad spend needs determinism.** The rules give the same verdict every run plus
   a written `Reason` per row, so a change can be reviewed, diffed and
   reproduced. Model verdicts move when the model or the prompt moves, which
   makes "why did we negate these 400 terms last month?" unanswerable.
5. **Silent confident errors are the expensive kind.** A wrong-but-fluent answer
   *overwrote* the rule verdict. Negating a high-intent term loses revenue;
   keeping a wasted one burns budget.
6. **Ops cost for nothing.** A local model means a daemon (llama.cpp / LM Studio
   / vLLM / Ollama), RAM, model pulls, cached verdicts and one more failure mode -
   for a few hundred three-word strings that a few hundred regexes classify in
   microseconds.

### What replaced it: the review list

`UNKNOWN` is not a dead end, it is a to-do list. The dashboard's **Needs review -
turn these into signal terms** table (backend function `keywords_for_review`)
lists those rows biggest spend first, with the engine's `Reason`. The workflow:

1. read the top terms in that table,
2. decide the intent once,
3. add the term(s) to the matching list in `config.py` (`service_terms`,
   `booking_terms`, `high_intent_terms`, `irrelevant_terms`, `product_terms`,
   `off_brand_terms`) - or override per query through `backend_function_args`,
4. refresh the query: the row leaves the review list and never returns.

The list shrinks as the vocabulary is learned, every decision is explicit and
reviewable, and nothing is guessed. If a model is ever wanted here, the useful
place is *offline* - cluster the review list and propose candidate terms for a
human to approve - never in the serving path.

## Efficiency & cost

The dashboard re-runs on every refresh, so the two quota-limited steps are cached:

| Cache | Keyed by | Default TTL | Config |
| --- | --- | --- | --- |
| Resolved Google Ads API version | developer token | 15 min | `google_ads_cache_ttl` |
| Google Ads keyword/metrics response | account + resource + date range + limit | 15 min | `google_ads_cache_ttl` |

Set a TTL to `0` to disable that cache:

```bash
bench --site <site> set-config google_ads_cache_ttl 0
```

Classification itself is pure CPU over a few hundred short strings - no network,
no model, no cache required - and it always returns the same answer for the same
input, so a dashboard refresh costs exactly one API call.

## Deploying / seeding

The **code** ships with the app; the dashboard is a database record, so it is
materialised on migrate by a patch - nobody has to build it by hand:

```bash
bench --site <site> migrate          # runs insights.patches.seed_google_ads_keyword_dashboard
bench build --app insights           # rebuilds the frontend (sidebar entry)
```

The seeder is idempotent by workbook title, so re-running is safe.

Seed it manually (or recreate with `force=True` after changing a chart):

```bash
bench --site <site> execute insights.ads_keywords.setup.seed_google_ads_keyword_dashboard
```

## Query args

Each backend function accepts a JSON object (`backend_function_args`). By default
the keywords come **live from the Google Ads API**:

```json
{
  "resource": "search_term_view",
  "date_range": "LAST_30_DAYS",
  "limit": 500
}
```

* `resource` - `search_term_view` (default, what users actually typed) or
  `keyword_view` (the keywords you bid on).
* `date_range` - any Google Ads date-range literal (`LAST_7_DAYS`, `LAST_30_DAYS`,
  `THIS_MONTH`, ...).
* `customer_id` - override the account configured in Google Ads Settings.

The four backend functions behind the dashboard:

| Function | Returns |
| --- | --- |
| `classify_keywords` | one row per keyword: Intent, Confidence, Reason, Suggested action |
| `keyword_intent_summary` | count + share per intent (the bar chart) |
| `negative_keyword_candidates` | only `POTENTIAL_NEGATIVE`, biggest spend first |
| `keywords_for_review` | only `UNKNOWN`, biggest spend first (`"max_confidence": 0.6` also pulls in the low-confidence rows) |

To classify a pasted list instead of live data, pass `keywords` (a JSON list) or
`keywords_text` (CSV / one keyword per line, optional `impressions` and `cost`
columns; a header row is auto-detected).

Every signal list in `config.py` can also be replaced per query, which is the way
to try a rule change before committing it:

```json
{
  "service_terms": ["massage", "urut", "tuina", "lomi lomi"],
  "off_brand_terms": ["video", "escort", "slot"],
  "product_terms": ["chair", "gun", "mat", "free"]
}
```
