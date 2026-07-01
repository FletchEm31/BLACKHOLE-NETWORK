# WeatherBHN — Kalshi Ticker Architecture Rule

**Adopted:** 2026-07-01  
**Status:** HARD RULE — applies to all new and existing code

---

## The Rule

**All internal references to a Kalshi contract must use the real `market_ticker` returned by the Kalshi API and stored in `weather_bronze_kalshi_market_snapshots`. Never construct a synthetic ticker string internally.**

---

## Background

Early CP4 implementation constructed a `contract_ticker` in a BHN-internal format:

| | Example |
|---|---|
| **BHN synthetic (wrong)** | `KXHIGHLAX-26JUL02-71-72` |
| **Kalshi native (correct)** | `KXHIGHLAX-26JUL01-B71.5` |

The two formats differ in:
- **Date component:** BHN used settlement date (`26JUL02`); Kalshi uses measurement date (`26JUL01`)
- **Bucket notation:** BHN used `floor-cap` (`71-72`); Kalshi uses center-point with `B` prefix (`B71.5`)

The BHN format was a tracking label. It is wrong for any Kalshi API call. Any live order placement using the synthetic ticker will receive a 400/404 from Kalshi.

---

## Required Lookup Pattern

Whenever code needs to reference a Kalshi contract, look up the real ticker from the snapshot table:

```python
with conn.cursor() as cur:
    cur.execute("""
        SELECT DISTINCT ON (station_code, bucket_label, target_date) market_ticker
        FROM weather_bronze_kalshi_market_snapshots
        WHERE station_code = %s
          AND bucket_label  = %s
          AND target_date   = %s
        ORDER BY station_code, bucket_label, target_date, retrieved_at DESC
    """, (station_code, bucket_label, target_date))
    row = cur.fetchone()
kalshi_ticker = row['market_ticker']  # always use this — never construct it
```

**Join key:** `(station_code, bucket_label, target_date)` — both sides use settlement date as `target_date`. The `bucket_label` format in the snapshot table is `floor-cap` (e.g., `71-72`), matching the exits table.

---

## Applies To

| Component | Required change |
|---|---|
| `weather_position_exits.contract_ticker` | Store real `market_ticker` from snapshot lookup, not synthetic string |
| `scripts/weather/cp4_kelly_sizer.py` | Look up `market_ticker` before writing to exits; pass it as `contract_ticker` |
| `core_trading_orchestrator.py` | Signal logging must write real `market_ticker` into `contract_ticker` |
| `scripts/trading/weather_position_monitor.py` — `_place_exit_order()` | Already identified as TICKET-W1; must look up `market_ticker`, NOT pass `p.contract_ticker` |
| Entry order placement (not yet written) | Must use snapshot lookup pattern from day one |
| Dashboard views + DBeaver queries | Once exits stores real tickers, `kalshi_market_ticker` join column becomes redundant — views can use `contract_ticker` directly |

---

## Migration Path

**For existing 9 paper-trade rows in `weather_position_exits`:**  
Synthetic tickers are already written. These rows will settle correctly because the scorer uses `(station_code, target_date, bucket_label)` — not `contract_ticker` — to match actuals. No emergency backfill required; correct tickers will appear automatically for any new positions after CP4 is fixed.

**Order of changes:**
1. Fix CP4 to look up and store real `market_ticker` in `contract_ticker`
2. Fix `weather_position_monitor._place_exit_order()` (TICKET-W1)
3. Update views to drop the redundant `kalshi_market_ticker` lateral join (optional cleanup after step 1)

---

## Never Do This

```python
# WRONG — synthetic BHN format
contract_ticker = f"KXHIGH{station_code[1:]}-{settlement_date_str}-{bucket_floor}-{bucket_cap}"

# WRONG — offset arithmetic
kalshi_date = target_date - timedelta(days=1)
contract_ticker = f"...{kalshi_date.strftime('%y%b%d').upper()}..."
```

Both patterns are banned. There is no correct way to construct a Kalshi ticker from parts. Always query.
