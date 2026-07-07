# WeatherBHN — Table Cleanup Resolution (2026-07-06/07)

**Supersedes/extends:** `WEATHERBHN-TABLE-INVENTORY-2026-07-03.md` for the tables actually touched below. All other verdicts in the 7/3 doc stand unchanged.

**Context:** A Postgres disk-exhaustion outage on 2026-07-06 was traced to `weather_contract_prices` — a pre-medallion duplicate of `weather_bronze_kalshi_market_snapshots`, dual-written since the Bronze/Silver/Gold rebuild shipped and never decommissioned. This closes out the full cleanup the 7/3 audit scoped but didn't finish: fresh inventory, classification, archive-then-drop of dead/duplicate tables, and a retention policy for the growing tables so this doesn't happen again.

**Method:** Read-only SSH+psql against BHN-LOSANGELES-US1, live grep across `/opt/bhn/trading` and `/opt/bhn-weather` (not just the git repo), cross-checked against `systemctl list-timers`. Everything destructive gated behind explicit operator sign-off.

---

## Decision log

| Timestamp (UTC) | Event |
|---|---|
| 2026-07-06 ~19:54–20:00 | `weather_contract_prices` archived (`pg_dump -Fc` → `/mnt/eh-hdd-cold/backups/weather_contract_prices-archive-20260706-1954.dump`, 1.4GB) and dropped from `eventhorizon`, by an external process prior to this session. Dual-write removed from `scripts/trading/weather_data_collector.py`'s `fetch_kalshi_markets()` around the same time, but left uncommitted and undocumented; header comment prematurely claimed "archived + dropped, see decision log" (no such log existed yet — this file is that log). |
| 2026-07-07 ~00:33–01:22 | Discovered `bhn-kalshi-prices.service` (always-on 5s Kalshi price poller) had been silently erroring every ~5-8s since the drop — it imported the pre-fix `fetch_kalshi_markets()` into memory at its Jul 3 startup and was never restarted. ~1,946+ `ERROR: relation "weather_contract_prices" does not exist` lines in the Postgres log. Non-fatal (caught, logged at debug level in the app), but continuous log spam for ~5 hours. |
| 2026-07-07 01:22:49 | `bhn-kalshi-prices.service` restarted — error loop confirmed stopped. |
| 2026-07-07 01:23 | `bhn-weather-position-monitor.timer` and `bhn-weather-edge-calculator.timer` re-enabled. **History note:** the 2026-06-30 02:47:47 UTC shutdown of both timers was intentional — part of implementing the Phase 1-4 / CP1-4 restructure and edge-calculator retirement — but stop-loss staying disabled for a full week afterward was not a deliberate ongoing decision. Re-enabled after being left off longer than intended. |
| 2026-07-07 01:24–01:37 | Retroactively verified the existing `weather_contract_prices` dump: restored into scratch DB `weatherbhn_archive_verify_weather_contract_prices`, 10,958,597 rows restored cleanly, structure intact (table/sequence/constraints/3 indexes/ACLs/comment all present in the TOC). No independent pre-drop row count exists to compare against (the drop predates any tooling), but the count is consistent with growth from 7.06M on 2026-07-03. Logged retroactively in the new `weather_table_archive_log` table. |
| 2026-07-07 01:33–01:37 | `weather_silver_model_base` (0 rows, confirmed dead — see correction below) archived and restore-verified (0=0 rows match). |
| 2026-07-07 ~01:35 | **Correction to the 7/3 audit**: live grep on LA found `weather_bets` (also flagged as a 0-row drop candidate on 7/3) actively referenced by `prediction_signal.py` (INSERT), `prediction_settlement.py` (settlement read/UPDATE), and `strategy_prediction_alpha.py` (audit logging) — a live, currently-used Strategy-9/prediction-alpha audit-trail table. 0 rows because it's early-phase/dry-run, not because it's dead. **Excluded from this cleanup**, reclassified live & unique. |
| 2026-07-07 01:33 | Deployed corrected `weather_data_collector.py` to `/opt/bhn/trading/` (fixed the stale/premature header comments), restarted `bhn-kalshi-prices.service` again to pick it up. Committed to git (commit `e9f1d4a`), along with pruning `weather_contract_prices`'s `CREATE TABLE`/`GRANT` entries from `sql/weather-schema.sql` and adding `scripts/bhn-weather-archive-table.sh`. |
| 2026-07-07 05:48–05:55 | Created `eh_cold_ts` tablespace on `/mnt/eh-hdd-cold/pg-tablespaces/eh_cold_ts`. Applied `sql/migrations/2026-07-07-partition-weather-bronze-kalshi-market-snapshots.sql` (partitioned `_new` replacement table, monthly partitions for 2026-06/07/08 + a `DEFAULT` safety-net partition). Backfilled via `bhn-weather-partition-backfill.sh` — 11,006,367 rows copied, split cleanly across `p2026_06` (5,677,844) and `p2026_07` (5,328,523) with 0 rows in `DEFAULT`. |
| 2026-07-07 ~05:52 | **New finding, not in original scope**: `weather_silver_market_conformed` had independently grown to 4.3GB/~10.1M rows in lockstep with the bronze table — a second large, ungoverned table. Investigated applying the same partitioning approach; found it carries a partial unique index (`is_latest_snapshot=TRUE`, one per `market_ticker`) that native time-partitioning would stop enforcing across partition boundaries (Postgres only enforces partial-unique constraints within a partition, not table-wide, unless the partition key is part of the index). `weather_edge_calculator.py` depends on this invariant via a `LIMIT 1` (no `ORDER BY`) lookup. Operator chose **archive-and-delete of old non-latest rows** instead of partitioning for this table — no risk to the invariant, reuses the same audit-log pattern. Built `scripts/bhn-weather-silver-market-archive.sh`; installed and test-run with a 90-day cutoff (0 eligible rows currently — nothing is old enough yet). |

---

## Final resolved status

| Table | Bucket | Action taken | Status |
|---|---|---|---|
| `weather_contract_prices` | Live & duplicate | Archived (dump verified 10,958,597 rows) + dropped | **Dropped** 2026-07-06 ~20:01 UTC |
| `weather_silver_model_base` | Dead & drop candidate | Archived + restore-verified (0=0 rows) | **Archived, DROP pending explicit operator sign-off** |
| `weather_bets` | ~~Dead & drop candidate~~ → **Live & unique** | None — reclassified | **Excluded from cleanup**, live audit-trail table |
| `weather_bronze_kalshi_market_snapshots` | Growing & unmanaged | Converted to native partitioning (monthly, range on `retrieved_at`) + `eh_cold_ts` cold tablespace for future aged-out partitions. 90-day retention cutoff. Backfill complete and verified. | **Cutover pending Gate E sign-off** (stops the live collector timer for the swap) |
| `weather_silver_market_conformed` | Growing & unmanaged (new finding) | Archive-and-delete tooling built for old non-latest rows (90-day cutoff), NOT partitioned (would break the `is_latest_snapshot` invariant) | **Tooling live, first real run pending once data ages past 90 days** |
| `weather_forecasts` | Unconfirmed (7/3) → confirmed live | None — freshness check confirmed active writes as of 2026-07-07 | No action (out of scope either way) |
| `weather_bronze_nbm_snapshots` | Unconfirmed (7/3) → confirmed live | None — freshness check confirmed active writes | No action |
| `weather_bronze_era5_kmia`, `weather_commodity_signals`, `weather_silver_calibration_training_set`, `weather_bronze_noaa_daily_actuals`, `weather_bronze_noaa_hourly_normals` | Dead & harmless scaffolding | None — reconfirmed still true | No action |
| `bhn-weather-position-monitor.timer`, `bhn-weather-edge-calculator.timer` | N/A (adjacent finding) | Re-enabled | **Live again** as of 2026-07-07 01:23 UTC |

## Disk-expansion status

The provider-level +250GB disk expansion (applied during tonight's crash recovery) was already fully reflected at the filesystem level by the time this session checked: `/dev/vdb` (250GB) → LUKS mapper `eh-nvme` (250GB) → XFS `/mnt/eh-nvme-hot` (250GB, 26% used). No `cryptsetup resize`/`xfs_growfs` was needed.

## Tooling added

- `scripts/bhn-weather-archive-table.sh` — reusable archive-then-drop (detached `pg_dump -Fc` + completion marker, restore-verify into a scratch DB, gated `drop`).
- `scripts/bhn-weather-partition-backfill.sh`, `bhn-weather-partition-cutover.sh`, `bhn-weather-partition-maintenance.sh` (+ systemd unit pair) — bronze-table partitioning migration and ongoing monthly hot/cold relocation.
- `scripts/bhn-weather-silver-market-archive.sh` — archive-and-delete tooling for `weather_silver_market_conformed`'s old non-latest rows.

## Open follow-ups (not touched this pass)

- `scripts/weather-collectors/weather_data_collector.py` (Hillsboro/Helsinki collector-node copy) still has the old `weather_contract_prices` dual-write code intact, but confirmed inert — neither node's systemd units ever invoke `--source kalshi_markets`. Left untouched; worth reconciling the two collector copies in a future session.
- Old, non-provisioned Grafana dashboard `infrastructure/docs/grafana/kalshi-live-dashboard.json` (queries `weather_contract_prices`/`weather_forecasts`/`weather_observations`/`prediction_contracts` directly) — not addressed this pass, per operator direction to keep this pass disk-focused.
- `weather_model_accuracy` hasn't written a new row since 2026-07-03 despite its settlement-recon timer firing since then — likely a downstream consequence of the same disruption chain as the timer shutdown; not independently investigated this pass.
