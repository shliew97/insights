# Customer Retention (cohort) dashboard

Implementation of the *Customer Retention Dashboard Developer Brief* as
reusable functionality inside the **Insights** app.

## Layout

| File | Responsibility |
| --- | --- |
| `config.py` | Every tunable business rule (windows, scopes, thresholds, timezone, interval buckets). |
| `engine.py` | Pure cohort engine - no `frappe` import, fully unit-tested. |
| `sources.py` | Maps the logical dataset onto HealthLand POS (`Booking` / `Member` / `Outlet` / `Staff`). |
| `backend_functions.py` | Whitelisted entry points exposed as `Insights Query v3` **backend functions**. |
| `setup.py` | Seeds the workbook, charts and dashboard into Insights. |
| `tests/test_engine.py` | The acceptance checks from the brief. |

## Business rules implemented

* A **qualifying visit** is a completed service visit; cancellations, no-shows,
  future bookings and purchases without attendance are excluded.
* Same-day bookings/treatments collapse into a single **visit-day**.
* A **cohort** is the set of customers sharing the same first-visit month;
  HealthLand and SOMA keep independent first-visit dates and cohorts.
* Cumulative windows count a return on days `1..W`; a cell is only shown when
  the whole cohort has the full follow-up window, otherwise **Not yet mature**.
* Monthly retention (not cumulative) counts unique customers per calendar month;
  only fully completed months through the cutoff are shown.
* Drill-down distinguishes **brand return**, **same-provider return** and the
  exclusive second-visit destination breakdown (same / different / unknown or
  multiple / no brand return).
* Data-quality indicators: cutoff, unresolved identities, unknown/multiple
  assignments, incomplete history coverage, immature windows, small samples.

## Running the tests

```bash
cd apps/insights
python -m unittest insights.customer_retention.tests.test_engine
```

## Deploying to another site / teammate

The **code** (engine, backend functions, sidebar entry) ships with the app. The
**dashboard itself is a database record**, so it is materialised on migrate by a
patch - nobody has to build it by hand in a workbook:

```bash
# 1. pull the app, then:
bench --site <site> migrate          # runs insights.patches.seed_customer_retention_dashboard
bench build --app insights           # rebuilds the frontend (sidebar entry)
```

The patch calls `seed_customer_retention_dashboard()`, which is idempotent by
workbook title, so re-running is safe.

Grant users access (they need the `Insights User` role; the workbook/dashboard
are shared with the organization by the seeder):

```bash
bench --site <site> execute insights.customer_retention.setup.ensure_insights_access
```

## Seeding the dashboard manually

```bash
bench --site <site> execute insights.customer_retention.setup.seed_customer_retention_dashboard
```

By default the seeded queries read real HealthLand POS data. Pass
`demo=True` in the query args (see `setup.DEFAULT_FILTERS`) to use deterministic
synthetic data instead.

## Query args

Each backend function accepts a JSON object (`backend_function_args`):

```json
{
  "brand": "HealthLand",
  "cohort_months": ["2025-12", "2026-01"],
  "window": 90,
  "scope": "brand",
  "from_date": "2025-01-01",
  "to_date": "2026-01-01",
  "cutoff": "2026-01-31",
  "small_sample_threshold": 30,
  "brand_map": {"SOMA KD": "SOMA"},
  "identity_map": {"old-id": "canonical-id"},
  "unresolved_identities": ["id-1"]
}
```
