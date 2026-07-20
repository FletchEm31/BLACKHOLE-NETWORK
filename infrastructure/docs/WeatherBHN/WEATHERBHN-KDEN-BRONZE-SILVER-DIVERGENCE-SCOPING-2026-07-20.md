# KDEN bronze/silver actuals divergence scoping — 2026-07-20

**This is a scoping document, not a fix. Nothing here has been implemented or corrected.**

## The finding

Found while re-settling the 348 resolved rows in `weather_model_accuracy` against NOAA daily actuals (see the settlement flip check, same session). For KDEN, 2026-06-11, the bronze and silver actuals tables disagree on the settled high temperature, with no audit trail explaining why:

| Table | `final_tmax_f` | `report_issued_at` | notes |
|---|---|---|---|
| `weather_bronze_nws_actuals` | **77.0°F** | 2026-06-12 07:33 UTC | matches NOAA daily actuals exactly (77.0°F) |
| `weather_silver_actuals_conformed` (`actual_source='nws_cli'`) | **90.0°F** | 2026-06-12 23:29 UTC | `created_at = 2026-06-12 07:34:30 UTC` — created right after the bronze row, but carries a *later* `report_issued_at` than bronze has on file |

**This is a different, separate issue from the two already found this session** — it postdates the VC-mislabeling contamination window (2026-01-01 to 2026-06-09; this is June 11) and is unrelated to the CLI preliminary-vs-finalized issue (that one is about report *type*, same value everywhere; this one is a genuine *value* mismatch between two tables that are supposed to be kept in sync).

## Why the code comment doesn't explain this

`weather_data_collector.py:1263` states plainly: *"Silver is idempotent (DO UPDATE) — write unconditionally on new bronze"* — `_populate_silver_actuals()` is only ever called immediately after a **new** bronze insert (`is_new=True`), and bronze itself uses `ON CONFLICT DO NOTHING` (first-ever value for a date wins, permanently). Under that logic, bronze and silver should always agree at the moment either one is written — silver should never show a later `report_issued_at` than what bronze has on file, since bronze would have rejected any later differing report as a conflict.

Yet here, silver's `report_issued_at` (23:29) is **later** than bronze's (07:33), with no bronze row anywhere corresponding to a 23:29 report. Something wrote 90.0°F into silver directly, attributing it to a report that left no trace in bronze — either bypassing the normal collector path entirely, or via a mechanism not accounted for in the code comment's stated invariant.

## What needs to be checked (not started)

1. **Scope of the divergence** — this was found on a single spot-check (KDEN, one date, surfaced only because it happened to be one of the 348 settled rows). Needs a systematic bronze-vs-silver diff across all `nws_cli` rows, all 6 stations with genuine CLI data, to find out if this is an isolated incident or a recurring pattern.
2. **Root cause** — find what process could have written 90.0°F into silver with a `report_issued_at` that has no bronze counterpart. Candidates to check: a manual one-off correction/backfill script (same shape as the VC-mislabeling root cause — a bulk operation outside the normal collector path), a bug in re-run/retry logic that skips the bronze insert but still calls the silver populate function, or something else not yet considered.
3. **Which value is actually correct** — NOAA agrees with bronze (77.0°F) for this one date, which is suggestive but not proof; NOAA itself can occasionally disagree with the true NWS CLI value (see the KPHX findings earlier this session, up to 8°F off on some dates). Don't assume bronze/NOAA are automatically right and silver is automatically wrong without checking the actual NWS CLI record for June 11, 2026 at Denver directly.
4. **Settlement impact** — this specific date (2026-06-11) is one of the two confirmed real settlement-outcome flips found in the 348-row check (`KXHIGHDEN-26JUN11-T83`: settled using silver's 90.0°F as YES for an ">83" threshold contract; the true value, 77.0°F, would have settled NO). That flip is already documented in the settlement-check conversation; this doc is about the underlying data divergence that caused it, not a duplicate of the settlement finding itself.

## Not yet checked

- Whether this affects any other station/date pairs beyond the one found.
- Whether `weather_gold_city_day_features` or CP3 training data ingested the 90.0°F silver value for this date (gold's `actual_tmax_f` for KDEN 2026-06-11 hasn't been checked against this specific finding yet).
