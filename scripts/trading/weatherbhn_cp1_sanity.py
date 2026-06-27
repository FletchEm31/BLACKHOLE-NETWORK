"""
WeatherBHN Checkpoint 1 — Data Sanity and Ticker Parsing.

Reads active contracts from weather_bronze_kalshi_market_snapshots, parses
Kalshi KXHIGH tickers into structured metadata, and applies sanity gates.

Ticker format:
  KXHIGH{STN}-{YYMONDD}-T{nn}   ceiling  → high bound = nn-1, range (-inf, nn-1)
  KXHIGH{STN}-{YYMONDD}-B{nn}   bracket  → range (nn, nn+0.99)

Examples:
  KXHIGHMIA-26JUN13-T86  → (KMIA, 2026-06-13, ceiling, -inf,  85.0)
  KXHIGHDEN-26JUN13-B92  → (KDEN, 2026-06-13, bracket,  92.0, 92.99)

Sanity gates (order matters — first failure reason is recorded):
  NULL_PRICES       — any of yes_bid/yes_ask/no_bid/no_ask is NULL
  OUT_OF_RANGE      — any price outside (0, 1)
  NEGATIVE_SPREAD   — yes_ask < yes_bid or no_ask < no_bid
  STRUCTURAL_BIAS_FLOOR — both yes_ask AND no_ask < 0.15 (pricing anomaly)
  SETTLEMENT_UNCERTAINTY — NWS issued AMENDED CLI for this station recently

Output: DataFrame with all parsed fields plus sanity_passed (bool) and
failure_reason (str | None). Downstream consumes only sanity_passed == True rows.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DSN = os.environ.get("BHN_PG_DSN", "host=/var/run/postgresql dbname=bhn user=ehuser")

# 3-letter ticker suffix → 4-letter ICAO station code
STATION_MAP: dict[str, str] = {
    "MIA": "KMIA",
    "DEN": "KDEN",
    "PHX": "KPHX",
    "LAX": "KLAX",
    "DFW": "KDFW",
    "NYC": "KNYC",
    "ORD": "KORD",
    "AUS": "KAUS",
}

MONTH_MAP: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Structural bias floor: if BOTH yes_ask and no_ask are below this, skip
STRUCTURAL_BIAS_FLOOR_THRESHOLD = 0.15

# How far back to look for NWS AMENDED CLI reports
AMENDMENT_LOOKBACK_DAYS = 3

_TICKER_RE = re.compile(
    r"^KXHIGH([A-Z]{3})-(\d{2})([A-Z]{3})(\d{2})-(T|B)(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────
# Ticker parsing
# ─────────────────────────────────────────────────────────────────────────

def parse_ticker(ticker: str) -> Optional[dict]:
    """Parse a KXHIGH ticker into structured metadata.

    Returns None if the ticker is malformed or station is unrecognised.
    """
    m = _TICKER_RE.match(ticker.strip().upper())
    if not m:
        return None

    stn3, yy, mon, dd, bucket_type_char, num_str = m.groups()

    station_code = STATION_MAP.get(stn3)
    if station_code is None:
        return None

    month = MONTH_MAP.get(mon.upper())
    if month is None:
        return None

    try:
        target_date = date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None

    threshold = float(num_str)

    if bucket_type_char.upper() == "T":
        # Ceiling: YES if high < threshold → range (-inf, threshold-1)
        contract_type = "ceiling"
        bucket_floor  = None          # -inf
        bucket_cap    = threshold - 1.0
    else:
        # Bracket: YES if high == floor (whole-degree bucket)
        contract_type = "bracket"
        bucket_floor  = threshold
        bucket_cap    = threshold + 0.99

    return {
        "market_ticker":  ticker,
        "station_code":   station_code,
        "target_date":    target_date,
        "contract_type":  contract_type,
        "bucket_floor":   bucket_floor,
        "bucket_cap":     bucket_cap,
    }


# ─────────────────────────────────────────────────────────────────────────
# DB reads
# ─────────────────────────────────────────────────────────────────────────

def fetch_active_snapshots(conn) -> list[dict]:
    """Fetch the latest snapshot per active market from the bronze orderbook."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (market_ticker)
                market_ticker,
                yes_bid, yes_ask,
                no_bid,  no_ask,
                volume,  open_interest,
                market_status,
                retrieved_at
            FROM weather_bronze_kalshi_market_snapshots
            WHERE market_status = 'active'
            ORDER BY market_ticker, retrieved_at DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_amended_stations(conn, lookback_days: int = AMENDMENT_LOOKBACK_DAYS) -> set[str]:
    """Return station codes where NWS issued an AMENDED CLI recently.

    Reads weather_bronze_nws_actuals for any rows flagged as amended
    within the lookback window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT station_code
            FROM weather_bronze_nws_actuals
            WHERE is_amended = TRUE
              AND retrieved_at >= %s
            """,
            (cutoff,),
        )
        return {row[0] for row in cur.fetchall()}


# ─────────────────────────────────────────────────────────────────────────
# Sanity checks
# ─────────────────────────────────────────────────────────────────────────

def _prices_valid(row: dict) -> tuple[bool, Optional[str]]:
    """Check that all four price columns are present and in (0, 1)."""
    for col in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
        v = row.get(col)
        if v is None:
            return False, "NULL_PRICES"
        v = float(v)
        if not (0.0 < v < 1.0):
            return False, f"OUT_OF_RANGE({col}={v:.4f})"
    return True, None


def _spread_valid(row: dict) -> tuple[bool, Optional[str]]:
    """Check spreads are non-negative."""
    yes_bid, yes_ask = float(row["yes_bid"]), float(row["yes_ask"])
    no_bid,  no_ask  = float(row["no_bid"]),  float(row["no_ask"])
    if yes_ask < yes_bid:
        return False, f"NEGATIVE_SPREAD(yes_ask={yes_ask:.4f}<yes_bid={yes_bid:.4f})"
    if no_ask < no_bid:
        return False, f"NEGATIVE_SPREAD(no_ask={no_ask:.4f}<no_bid={no_bid:.4f})"
    return True, None


def _structural_bias_check(row: dict) -> tuple[bool, Optional[str]]:
    """Flag if both yes_ask and no_ask are below the floor threshold.

    A market where both YES and NO asks are < 0.15 implies the maker is
    offering a near-guaranteed arb; route to CP2 instead of the model.
    """
    yes_ask = float(row["yes_ask"])
    no_ask  = float(row["no_ask"])
    if yes_ask < STRUCTURAL_BIAS_FLOOR_THRESHOLD and no_ask < STRUCTURAL_BIAS_FLOOR_THRESHOLD:
        return False, (
            f"STRUCTURAL_BIAS_FLOOR("
            f"yes_ask={yes_ask:.3f} no_ask={no_ask:.3f} "
            f"both<{STRUCTURAL_BIAS_FLOOR_THRESHOLD})"
        )
    return True, None


# ─────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────

def run_cp1(conn) -> pd.DataFrame:
    """Run CP1 sanity check over all active orderbook snapshots.

    Returns a DataFrame with columns:
      market_ticker, station_code, target_date, contract_type,
      bucket_floor, bucket_cap,
      yes_bid, yes_ask, no_bid, no_ask,
      volume, open_interest,
      sanity_passed, failure_reason
    """
    snapshots      = fetch_active_snapshots(conn)
    amended_stns   = fetch_amended_stations(conn)

    records = []
    for snap in snapshots:
        ticker = snap["market_ticker"]

        # Parse ticker
        parsed = parse_ticker(ticker)
        if parsed is None:
            records.append({
                **snap,
                "station_code":  None,
                "target_date":   None,
                "contract_type": None,
                "bucket_floor":  None,
                "bucket_cap":    None,
                "sanity_passed": False,
                "failure_reason": f"UNPARSEABLE_TICKER({ticker})",
            })
            continue

        row = {**snap, **parsed}

        # Gate 1: price presence and range
        ok, reason = _prices_valid(row)
        if not ok:
            records.append({**row, "sanity_passed": False, "failure_reason": reason})
            continue

        # Gate 2: spread sanity
        ok, reason = _spread_valid(row)
        if not ok:
            records.append({**row, "sanity_passed": False, "failure_reason": reason})
            continue

        # Gate 3: structural bias floor — both asks suspiciously cheap (route to CP2)
        ok, reason = _structural_bias_check(row)
        if not ok:
            records.append({**row, "sanity_passed": False, "failure_reason": reason})
            continue

        # Gate 4: NWS settlement uncertainty
        if row["station_code"] in amended_stns:
            records.append({
                **row,
                "sanity_passed":  False,
                "failure_reason": f"SETTLEMENT_UNCERTAINTY({row['station_code']} amended CLI)",
            })
            continue

        records.append({**row, "sanity_passed": True, "failure_reason": None})

    df = pd.DataFrame(records)

    passed  = df["sanity_passed"].sum() if len(df) else 0
    failed  = (~df["sanity_passed"]).sum() if len(df) else 0
    log.info("CP1 complete: %d passed, %d failed (total=%d)", passed, failed, len(df))

    if failed > 0 and len(df):
        reasons = (
            df.loc[~df["sanity_passed"], "failure_reason"]
            .value_counts()
            .head(5)
        )
        log.info("CP1 failure reasons:\n%s", reasons.to_string())

    return df


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("WeatherBHN CP1 — starting sanity check against live Bronze data")
    with psycopg2.connect(DSN) as conn:
        df = run_cp1(conn)

    print("\n=== CP1 OUTPUT SAMPLE (first 20 rows) ===")
    cols = [
        "market_ticker", "station_code", "target_date", "contract_type",
        "bucket_floor", "bucket_cap",
        "yes_ask", "no_ask",
        "sanity_passed", "failure_reason",
    ]
    display_cols = [c for c in cols if c in df.columns]
    print(df[display_cols].head(20).to_string(index=False))

    print("\n=== CP1 PASSED CONTRACTS ===")
    passed = df[df["sanity_passed"]]
    print(f"  {len(passed)} contracts cleared sanity gate")

    print("\n=== CP1 FAILED CONTRACTS ===")
    failed = df[~df["sanity_passed"]]
    if len(failed):
        print(failed[["market_ticker", "failure_reason"]].to_string(index=False))
    else:
        print("  None")


if __name__ == "__main__":
    main()
