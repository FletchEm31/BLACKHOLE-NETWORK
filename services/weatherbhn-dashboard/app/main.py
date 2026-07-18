"""WeatherBHN Trading Dashboard — FastAPI backend.

Read-only planning/simulation tool. Does NOT place orders, does NOT touch
DRY_RUN/enabled flags, does NOT read or write any CP1-4 decision logic.
Scope: KDEN, KLAX, KMIA (the 3 tradeable cities) -- CITIES below lists all
8 BHN weather stations with an `enabled` flag so adding a future city is a
config change, not a rebuild, per operator scope note.
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, fees, model_math

app = FastAPI(title="WeatherBHN Trading Dashboard")

CITIES = [
    {"station_code": "KDEN", "city": "Denver",             "timezone": "America/Denver",      "enabled": True},
    {"station_code": "KLAX", "city": "Los Angeles",         "timezone": "America/Los_Angeles",  "enabled": True},
    {"station_code": "KMIA", "city": "Miami",               "timezone": "America/New_York",     "enabled": True},
    {"station_code": "KPHX", "city": "Phoenix",             "timezone": "America/Phoenix",      "enabled": False},
    {"station_code": "KDFW", "city": "Dallas-Fort Worth",   "timezone": "America/Chicago",      "enabled": False},
    {"station_code": "KNYC", "city": "New York",            "timezone": "America/New_York",     "enabled": False},
    {"station_code": "KORD", "city": "Chicago",             "timezone": "America/Chicago",      "enabled": False},
    {"station_code": "KAUS", "city": "Austin",              "timezone": "America/Chicago",      "enabled": False},
]
ENABLED_STATIONS = {c["station_code"] for c in CITIES if c["enabled"]}

SIGMA_MARKERS = list(range(-4, 5))  # -4sigma .. +4sigma


@app.on_event("startup")
def _startup():
    db.init_pool()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    return {"cities": CITIES}


# ---------------------------------------------------------------------------
# Ladder + reference-strip + volume table
# ---------------------------------------------------------------------------

def _bucket_sort_key(b: dict) -> float:
    floor, cap = b.get("bucket_floor"), b.get("bucket_cap")
    if floor is not None:
        return float(floor)
    if cap is not None:
        return float(cap) - 0.01  # T-low ("<=cap") sorts just below the lowest between-bucket
    return 0.0


def _chance_pct(b: dict) -> Optional[float]:
    if b.get("last_price") is not None:
        lp = float(b["last_price"])
        return round((lp if lp <= 1.0 else lp / 100.0) * 100, 1)
    yes_bid, no_ask = b.get("yes_bid"), b.get("no_ask")
    if yes_bid is not None and no_ask is not None:
        return round(((float(yes_bid) + (1 - float(no_ask))) / 2) * 100, 1)
    return None


@app.get("/api/ladder")
def get_ladder(station: str = Query(...), target_date: date = Query(...)):
    if station not in {c["station_code"] for c in CITIES}:
        raise HTTPException(400, f"unknown station {station!r}")

    with db.conn_cursor() as cur:
        # Mirrors cp4_kelly_sizer.run_cp4_kelly()'s own bucket query exactly:
        # pin to the single latest retrieved_at batch for this station/date
        # (not "latest per bucket_label ever seen" -- that was the bug: a
        # bucket Kalshi stopped quoting would linger forever with stale
        # prices instead of dropping out), plus the same 45-minute
        # staleness cutoff CP4 uses (collector runs ~33 min; one cycle of
        # headroom before declaring data stale).
        cur.execute("""
            SELECT bucket_label, bucket_type, bucket_floor, bucket_cap,
                yes_bid, yes_ask, no_bid, no_ask, yes_mid, last_price,
                volume, open_interest, market_ticker, market_status, retrieved_at
            FROM weather_bronze_kalshi_market_snapshots
            WHERE station_code = %s AND target_date = %s AND contract_side = 'high'
              AND retrieved_at = (
                  SELECT MAX(retrieved_at)
                  FROM weather_bronze_kalshi_market_snapshots
                  WHERE station_code = %s AND target_date = %s AND contract_side = 'high'
              )
              AND retrieved_at >= NOW() - INTERVAL '45 minutes'
        """, (station, target_date, station, target_date))
        bucket_rows = cur.fetchall()

        # A future/not-yet-listed market has zero snapshot rows for a
        # legitimate reason (Kalshi hasn't opened it, so there's nothing to
        # poll) -- that's not the same failure as a listed market the
        # collector has stopped reporting on. prediction_contracts is
        # populated by every discovery poll independent of whether price
        # snapshotting succeeds, so it tells listed-vs-not apart from the
        # snapshot table alone. Only flag data_stale when a market is
        # actually listed and we still have no fresh bucket rows for it.
        cur.execute("""
            SELECT 1 FROM prediction_contracts
            WHERE station_code = %s AND resolution_date = %s
              AND variable = 'tmax_f' AND is_active
            LIMIT 1
        """, (station, target_date))
        market_listed = cur.fetchone() is not None
        data_stale = market_listed and len(bucket_rows) == 0

        # Market open time: Kalshi exposes this directly in its raw payload
        # (open_time) -- captured all along in source_payload_json, just
        # never promoted to its own column. Queried independently of the
        # staleness filter above so it's still shown even if live pricing
        # has gone stale.
        cur.execute("""
            SELECT source_payload_json ->> 'open_time' AS open_time
            FROM weather_bronze_kalshi_market_snapshots
            WHERE station_code = %s AND target_date = %s AND contract_side = 'high'
            ORDER BY retrieved_at DESC LIMIT 1
        """, (station, target_date))
        open_time_row = cur.fetchone()
        market_open_time = open_time_row["open_time"] if open_time_row else None

        # Average time of daily high -- already computed in
        # weather_station_climatology (build_station_climatology_2026_07_17.py),
        # just surfaced here, no new calculation.
        cur.execute("""
            SELECT average_dailyhigh_time_local, average_dailyhigh_time_utc, local_timezone, source_station_note
            FROM weather_station_climatology
            WHERE station_code = %s AND month = %s
        """, (station, target_date.month))
        clim_row = cur.fetchone()

        # side = 'NO' added 2026-07-18b: this ladder is CP4's NO-side view.
        # Without the filter, once a YES-side row can exist for the same
        # station/date, ORDER BY decision_timestamp DESC LIMIT 1 would
        # arbitrarily return whichever side was decided more recently, with
        # no side label surfaced to the frontend -- a real redesign (side
        # selector, or showing both), not fixed by this filter alone; this
        # just keeps today's NO-only behavior stable in the meantime.
        cur.execute("""
            SELECT final_entry_predicted_tmax_f, final_entry_sigma_used,
                   predicted_tmax_f, sigma_used, decision_timestamp,
                   hours_to_settle
            FROM weather_position_exits_clean
            WHERE station_code = %s AND target_date = %s AND side = 'NO'
            ORDER BY decision_timestamp DESC
            LIMIT 1
        """, (station, target_date))
        signal_row = cur.fetchone()

        # weather_position_exits only ever gets a row when a bucket
        # QUALIFIES as a trade (edge >= threshold) -- on a day/cycle where
        # nothing has qualified yet, signal_row is None even though CP4 has
        # evaluated every bucket. weather_gold_contract_ledger logs every
        # evaluated bucket (including SKIP), so it's a much more complete
        # mu source: predicted_tmax_f = nws_forecast_f + model_delta_f
        # (mirrors cp4_kelly_sizer.py's model_delta_f definition exactly).
        ledger_row = None
        if signal_row is None or (signal_row.get("final_entry_predicted_tmax_f") is None
                                   and signal_row.get("predicted_tmax_f") is None):
            cur.execute("""
                SELECT nws_forecast_f, model_delta_f, signal_generated_at
                FROM weather_gold_contract_ledger
                WHERE station_code = %s AND target_date = %s
                  AND nws_forecast_f IS NOT NULL AND model_delta_f IS NOT NULL
                ORDER BY signal_generated_at DESC
                LIMIT 1
            """, (station, target_date))
            ledger_row = cur.fetchone()

        base_sigma_row = None
        need_computed_sigma = signal_row is None or (
            signal_row.get("final_entry_sigma_used") is None
            and signal_row.get("sigma_used") is None
        )
        if need_computed_sigma:
            # Day-of-year shrinkage calibration (2026-07-18c) -- mirrors
            # cp3_inference.py's real base_rmse lookup exactly, so the
            # dashboard's "computed fresh" sigma preview matches what CP4
            # actually uses. Falls back to the season-bucket model_calibration
            # table if this station/day-of-year has no row yet.
            cur.execute("""
                SELECT blended_sigma AS rmse FROM weather_model_calibration_daily
                WHERE station_code = %s AND variable = 'tmax_f'
                  AND source_model = 'nws' AND lead_time_hours = 24
                  AND day_of_year = %s
            """, (station, target_date.timetuple().tm_yday))
            base_sigma_row = cur.fetchone()
            if base_sigma_row is None or base_sigma_row.get("rmse") is None:
                cur.execute("""
                    SELECT rmse FROM model_calibration
                    WHERE station_code = %s AND variable = 'tmax_f'
                      AND source_model = 'nws' AND lead_time_hours = 24
                      AND season = %s
                """, (station, model_math.season_for(target_date)))
                base_sigma_row = cur.fetchone()

    mu = sigma = None
    mu_source = sigma_source = "none"
    hours_to_settle = None
    if signal_row:
        hours_to_settle = signal_row.get("hours_to_settle")
        if signal_row.get("final_entry_predicted_tmax_f") is not None:
            mu, mu_source = float(signal_row["final_entry_predicted_tmax_f"]), "entry_frozen"
        elif signal_row.get("predicted_tmax_f") is not None:
            mu, mu_source = float(signal_row["predicted_tmax_f"]), "live"
        if signal_row.get("final_entry_sigma_used") is not None:
            sigma, sigma_source = float(signal_row["final_entry_sigma_used"]), "entry_frozen"
        elif signal_row.get("sigma_used") is not None:
            sigma, sigma_source = float(signal_row["sigma_used"]), "live"

    if mu is None and ledger_row is not None:
        mu = float(ledger_row["nws_forecast_f"]) + float(ledger_row["model_delta_f"])
        mu_source = "ledger_skip"  # bucket(s) evaluated this cycle, none qualified as a trade

    if sigma is None and base_sigma_row is not None and base_sigma_row.get("rmse") is not None:
        now_utc = datetime.now(timezone.utc)
        sigma = round(model_math.calculate_time_decayed_sigma(
            float(base_sigma_row["rmse"]), station, now_utc, target_date
        ), 4)
        sigma_source = "computed_fresh"  # same formula CP4 uses, computed here since no signal row exists yet
        if hours_to_settle is None:
            hours_to_settle = round(
                max((model_math.settlement_dt(station, target_date) - now_utc).total_seconds() / 3600.0, 0.0), 2
            )

    sigma_markers = []
    if mu is not None and sigma is not None:
        for n in SIGMA_MARKERS:
            sigma_markers.append({"n": n, "temp_f": round(mu + n * sigma, 1)})

    # Port of cp4_kelly_sizer.run_cp4_kelly()'s threshold-bucket-opening
    # logic, verbatim: Kalshi stores BOTH T-low and T-high threshold
    # buckets with bucket_floor == bucket_cap == threshold_value (e.g. T90
    # and T97 both literally store floor=cap). The smallest such value is
    # the bottom ("<=X", opens downward to -inf); the largest is the top
    # (">=X", opens upward to +inf). Without this, threshold buckets render
    # as a malformed "90-90" range instead of "90 or below" / "97 or
    # above", AND sigma-marker bucket-membership silently misattributes
    # any marker beyond the threshold (it would fall outside [X,X] instead
    # of the correct open-ended range).
    thresh_vals = sorted(
        float(b["bucket_floor"])
        for b in bucket_rows
        if b["bucket_type"] == "threshold"
        and b["bucket_floor"] is not None and b["bucket_cap"] is not None
        and float(b["bucket_floor"]) == float(b["bucket_cap"])
    )
    bottom_thresh = thresh_vals[0] if len(thresh_vals) >= 1 else None
    top_thresh = thresh_vals[-1] if len(thresh_vals) >= 2 else None

    buckets = []
    for b in sorted(bucket_rows, key=_bucket_sort_key):
        floor = float(b["bucket_floor"]) if b["bucket_floor"] is not None else None
        cap = float(b["bucket_cap"]) if b["bucket_cap"] is not None else None
        if b["bucket_type"] == "threshold" and floor is not None and floor == cap:
            if floor == bottom_thresh:
                floor = None   # "<=X" -- opens to -inf
            elif floor == top_thresh:
                cap = None     # ">=X" -- opens to +inf
        volume = float(b["volume"]) if b["volume"] is not None else None
        markers_in_bucket = []
        for m in sigma_markers:
            t = m["temp_f"]
            lo = floor if floor is not None else float("-inf")
            hi = cap if cap is not None else float("inf")
            if lo <= t <= hi:
                markers_in_bucket.append(m["n"])

        chance_pct = _chance_pct(b)
        model_prob_pct = edge_pct = None
        distribution_used = None
        if mu is not None and sigma is not None and sigma > 0:
            prob, distribution_used = model_math.calculate_bucket_probability(mu, sigma, floor, cap)
            model_prob_pct = round(prob * 100, 1)
            if chance_pct is not None:
                edge_pct = round(model_prob_pct - chance_pct, 1)

        buckets.append({
            "bucket_label":  b["bucket_label"],
            "bucket_type":   b["bucket_type"],
            "bucket_floor":  floor,
            "bucket_cap":    cap,
            "yes_bid_cents": round(float(b["yes_bid"]) * 100, 1) if b["yes_bid"] is not None else None,
            "yes_ask_cents": round(float(b["yes_ask"]) * 100, 1) if b["yes_ask"] is not None else None,
            "no_bid_cents":  round(float(b["no_bid"]) * 100, 1) if b["no_bid"] is not None else None,
            "no_ask_cents":  round(float(b["no_ask"]) * 100, 1) if b["no_ask"] is not None else None,
            "chance_pct":    chance_pct,
            "model_prob_pct": model_prob_pct,
            "edge_pct":      edge_pct,
            "distribution_used": distribution_used,
            "volume":        volume,
            "open_interest": float(b["open_interest"]) if b["open_interest"] is not None else None,
            "is_liquid":     (volume > 100.0) if volume is not None else None,
            "market_ticker": b["market_ticker"],
            "sigma_markers_in_bucket": markers_in_bucket,
            "retrieved_at":  b["retrieved_at"].isoformat() if b["retrieved_at"] else None,
        })

    market_close_dt = model_math.market_close_time_utc(station, target_date)

    return {
        "station_code": station,
        "target_date": target_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mu": mu, "mu_source": mu_source,
        "sigma": sigma, "sigma_source": sigma_source,
        "sigma_markers": sigma_markers,
        "hours_to_settle": float(hours_to_settle) if hours_to_settle is not None else None,
        "buckets": buckets,
        "data_stale": data_stale,
        "market_listed": market_listed,
        "market_open_time": market_open_time,
        "market_close_time": market_close_dt.isoformat() if market_close_dt else None,
        "avg_dailyhigh_time_local": (clim_row["average_dailyhigh_time_local"].isoformat()
                                      if clim_row and clim_row["average_dailyhigh_time_local"] else None),
        "avg_dailyhigh_time_utc": (clim_row["average_dailyhigh_time_utc"].isoformat()
                                    if clim_row and clim_row["average_dailyhigh_time_utc"] else None),
        "avg_dailyhigh_timezone": clim_row["local_timezone"] if clim_row else None,
        "avg_dailyhigh_source_note": clim_row["source_station_note"] if clim_row else None,
    }


# ---------------------------------------------------------------------------
# Fee preview (kept server-side too so backend/frontend never drift)
# ---------------------------------------------------------------------------

@app.get("/api/fee")
def get_fee(price_cents: float = Query(..., gt=0, lt=100),
            contracts: int = Query(..., ge=0),
            rate: str = Query("maker", pattern="^(maker|taker)$")):
    p = price_cents / 100.0
    fn = fees.maker_fee if rate == "maker" else fees.taker_fee
    return {"fee_usd": fn(p, contracts), "rate": rate}


# ---------------------------------------------------------------------------
# Manual trade journal (CRUD) — never touches any trading table
# ---------------------------------------------------------------------------

class JournalEntry(BaseModel):
    station_code: str
    target_date: date
    bucket_label: str
    side: str  # 'yes' | 'no'
    price_cents: float
    investment_usd: float
    contracts: int
    outcome: str = "pending"  # 'win' | 'loss' | 'pending'
    pnl_usd: Optional[float] = None
    notes: Optional[str] = None


@app.get("/api/journal")
def list_journal(station: Optional[str] = None):
    with db.conn_cursor() as cur:
        if station:
            cur.execute("""
                SELECT * FROM weatherbhn_dashboard_journal
                WHERE station_code = %s ORDER BY target_date DESC, id DESC
            """, (station,))
        else:
            cur.execute("SELECT * FROM weatherbhn_dashboard_journal ORDER BY target_date DESC, id DESC")
        rows = cur.fetchall()
    return {"entries": [dict(r) for r in rows]}


@app.post("/api/journal")
def create_journal_entry(entry: JournalEntry):
    if entry.side not in ("yes", "no"):
        raise HTTPException(400, "side must be 'yes' or 'no'")
    with db.conn_cursor() as cur:
        cur.execute("""
            INSERT INTO weatherbhn_dashboard_journal
                (station_code, target_date, bucket_label, side, price_cents,
                 investment_usd, contracts, outcome, pnl_usd, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (entry.station_code, entry.target_date, entry.bucket_label, entry.side,
              entry.price_cents, entry.investment_usd, entry.contracts,
              entry.outcome, entry.pnl_usd, entry.notes))
        row = cur.fetchone()
    return dict(row)


@app.put("/api/journal/{entry_id}")
def update_journal_entry(entry_id: int, entry: JournalEntry):
    with db.conn_cursor() as cur:
        cur.execute("""
            UPDATE weatherbhn_dashboard_journal
            SET station_code = %s, target_date = %s, bucket_label = %s, side = %s,
                price_cents = %s, investment_usd = %s, contracts = %s,
                outcome = %s, pnl_usd = %s, notes = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING *
        """, (entry.station_code, entry.target_date, entry.bucket_label, entry.side,
              entry.price_cents, entry.investment_usd, entry.contracts,
              entry.outcome, entry.pnl_usd, entry.notes, entry_id))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "journal entry not found")
    return dict(row)


@app.delete("/api/journal/{entry_id}")
def delete_journal_entry(entry_id: int):
    with db.conn_cursor() as cur:
        cur.execute("DELETE FROM weatherbhn_dashboard_journal WHERE id = %s RETURNING id", (entry_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "journal entry not found")
    return {"deleted": entry_id}


# ---------------------------------------------------------------------------
# Sigma-marker performance -- LIVE, growing computation over every settled
# trade (weather_position_exits_clean, scored_at IS NOT NULL), re-run on
# every request as more paper trades get made and graded. NOT a snapshot of
# WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md's numbers (that doc pooled
# into 7 zone-ranges from a fixed 87-trade dataset) -- this recomputes from
# scratch, binned by the same 9 integer sigma markers (-4..+4) the ladder
# and reference strip use, both pooled across all 3 cities AND cross-tabbed
# per city, so a city (e.g. KDEN, weaker forecast calibration) can be seen
# diverging from the pooled result at a specific marker.
#
# Cell highlighting is deliberately minimal (operator simplification
# 2026-07-18): only a positive-ROI cell gets colored, everything else
# (negative, zero, or no data) stays neutral -- no n-threshold gate, no
# separate red/thin/no_data states.
# ---------------------------------------------------------------------------


def _sigma_marker_for_trade(bucket_floor, bucket_cap, mu, sigma) -> int:
    """Same signed-distance-to-near-edge convention as
    WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md, rounded to the nearest
    integer marker and clamped to [-4, 4] (the same 9 markers the ladder
    shows)."""
    if bucket_floor is not None and mu < bucket_floor:
        signed_dist = bucket_floor - mu
    elif bucket_cap is not None and mu > bucket_cap:
        signed_dist = bucket_cap - mu
    else:
        signed_dist = 0.0
    z = signed_dist / sigma
    return max(-4, min(4, round(z)))


def _aggregate_cell(rows: list[dict]) -> dict:
    """tag is binary now, per operator simplification 2026-07-18: 'positive'
    (any positive ROI, any sample size) or 'neutral' (everything else,
    including no data). No n-threshold gate, no separate red/thin/no_data
    states -- those were removed as visual noise."""
    n = len(rows)
    wins = sum(1 for r in rows if r["win"])
    staked = sum(r["stake_usd"] for r in rows if r["stake_usd"] is not None)
    pnl = sum(r["pnl"] for r in rows if r["pnl"] is not None)
    win_pct = round(wins / n * 100, 1) if n else None
    roi_pct = round(pnl / staked * 100, 1) if staked else None

    tag = "positive" if (roi_pct is not None and roi_pct > 0) else "neutral"

    return {"n": n, "wins": wins, "win_pct": win_pct,
            "staked": round(staked, 2), "pnl": round(pnl, 2),
            "roi_pct": roi_pct, "tag": tag}


@app.get("/api/sigma-performance")
def get_sigma_performance():
    """0sigma is NOT resimulated as Yes anymore (reverted 2026-07-18 per
    operator direction) -- every marker, including 0, uses the plain
    recorded No-side outcome/pnl, same as every other marker."""
    with db.conn_cursor() as cur:
        cur.execute("""
            SELECT station_code, bucket_floor, bucket_cap,
                   final_entry_predicted_tmax_f, final_entry_sigma_used,
                   final_outcome, final_realized_pnl_usd,
                   entry_no_ask_cents, final_contracts_recommended
            FROM weather_position_exits_clean
            WHERE scored_at IS NOT NULL
              AND final_entry_predicted_tmax_f IS NOT NULL
              AND final_entry_sigma_used IS NOT NULL
              AND final_entry_sigma_used > 0
              AND station_code = ANY(%s)
        """, (list(ENABLED_STATIONS),))
        raw_rows = cur.fetchall()

    per_marker: dict[int, list[dict]] = {n: [] for n in SIGMA_MARKERS}
    per_city_marker: dict[str, dict[int, list[dict]]] = {
        c: {n: [] for n in SIGMA_MARKERS} for c in ENABLED_STATIONS
    }

    for r in raw_rows:
        mu = float(r["final_entry_predicted_tmax_f"])
        sigma = float(r["final_entry_sigma_used"])
        floor = float(r["bucket_floor"]) if r["bucket_floor"] is not None else None
        cap = float(r["bucket_cap"]) if r["bucket_cap"] is not None else None
        marker = _sigma_marker_for_trade(floor, cap, mu, sigma)

        contracts = r["final_contracts_recommended"]
        entry_no_ask_cents = r["entry_no_ask_cents"]
        stake_usd = (float(contracts) * float(entry_no_ask_cents) / 100.0
                     if contracts is not None and entry_no_ask_cents is not None else None)

        row = {
            "win": r["final_outcome"] == "NO_WIN",
            "pnl": float(r["final_realized_pnl_usd"]) if r["final_realized_pnl_usd"] is not None else None,
            "stake_usd": stake_usd,
        }
        per_marker[marker].append(row)
        per_city_marker[r["station_code"]][marker].append(row)

    return {
        "markers": SIGMA_MARKERS,
        "pooled": {str(n): _aggregate_cell(per_marker[n]) for n in SIGMA_MARKERS},
        "by_city": {
            city: {str(n): _aggregate_cell(per_city_marker[city][n]) for n in SIGMA_MARKERS}
            for city in ENABLED_STATIONS
        },
    }


# ---------------------------------------------------------------------------
# Paper Position Summary -- real system-placed paper trades from
# weather_position_exits, as distinct from the Simulation Summary panel's
# manual what-if entries (client-side only, never touches this table).
# ---------------------------------------------------------------------------

@app.get("/api/position-exits")
def get_position_exits(station: Optional[str] = Query(None)):
    """Full paper-trading history, no date filter -- operator direction
    2026-07-18: 'full paper-trading history... not scoped to the current
    city/date tab or any rolling window.' station is an optional display
    filter only, not a default scope -- omit it to see every city.

    result is side-aware: side='NO' wins when final_outcome='NO_WIN',
    side='YES' (none exist yet, but this is the actual bug the 2026-07-18b
    migration's UNIQUE(contract_ticker, side) + safe filters were built to
    let coexist) wins on the opposite outcome. OPEN for unscored rows
    (scored_at IS NULL), never guessed at.

    investment_usd is final_stake_usd_recommended directly, NOT bundled
    with fee_usd (unlike the Simulation Summary's client-side calcSide(),
    CP4's real stake sizing never included the fee to begin with -- see
    cp4_kelly_sizer.py's _maker_fee() docstring -- so no adjustment is
    needed here to keep Investment/Fee from double-counting).
    """
    where = "WHERE 1=1"
    params: list = []
    if station:
        where += " AND station_code = %s"
        params.append(station)

    with db.conn_cursor() as cur:
        cur.execute(f"""
            SELECT station_code, target_date, contract_ticker, bucket_label,
                   bucket_floor, bucket_cap, side,
                   final_contracts_recommended, final_stake_usd_recommended,
                   fee_usd, scored_at, final_outcome, final_realized_pnl_usd
            FROM weather_position_exits_clean
            {where}
            ORDER BY target_date DESC, decision_timestamp DESC
        """, params)
        rows = cur.fetchall()

    positions = []
    for r in rows:
        stake = float(r["final_stake_usd_recommended"]) if r["final_stake_usd_recommended"] is not None else None
        fee = float(r["fee_usd"]) if r["fee_usd"] is not None else 0.0
        pnl = float(r["final_realized_pnl_usd"]) if r["final_realized_pnl_usd"] is not None else None
        is_open = r["scored_at"] is None

        result = "OPEN"
        if not is_open:
            side_won = ((r["final_outcome"] == "NO_WIN") if r["side"] == "NO"
                        else (r["final_outcome"] == "NO_LOSS"))
            result = "WIN" if side_won else "LOSS"

        roi_pct = round((pnl / stake) * 100, 1) if (pnl is not None and stake) else None

        positions.append({
            "station_code":    r["station_code"],
            "target_date":     r["target_date"].isoformat(),
            "contract_ticker": r["contract_ticker"],
            "bucket_label":    r["bucket_label"],
            "bucket_floor":    float(r["bucket_floor"]) if r["bucket_floor"] is not None else None,
            "bucket_cap":      float(r["bucket_cap"]) if r["bucket_cap"] is not None else None,
            "side":            r["side"],
            "contracts":       r["final_contracts_recommended"],
            "investment_usd":  stake,
            "fee_usd":         fee,
            "result":          result,
            "pnl_usd":         pnl,
            "roi_pct":         roi_pct,
        })

    return {"positions": positions}


# ---------------------------------------------------------------------------
# Per-city scratch notepad -- freeform, separate from the structured trade
# journal above. One overwritable note per station (see
# sql/weatherbhn-dashboard-notes-schema.sql for why).
# ---------------------------------------------------------------------------

class NoteBody(BaseModel):
    note_text: str


@app.get("/api/notes/{station}")
def get_note(station: str):
    with db.conn_cursor() as cur:
        cur.execute("SELECT note_text, updated_at FROM weatherbhn_dashboard_notes WHERE station_code = %s", (station,))
        row = cur.fetchone()
    return {"note_text": row["note_text"] if row else "",
            "updated_at": row["updated_at"].isoformat() if row and row["updated_at"] else None}


@app.put("/api/notes/{station}")
def put_note(station: str, body: NoteBody):
    with db.conn_cursor() as cur:
        cur.execute("""
            INSERT INTO weatherbhn_dashboard_notes (station_code, note_text, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (station_code) DO UPDATE SET note_text = EXCLUDED.note_text, updated_at = NOW()
            RETURNING note_text, updated_at
        """, (station, body.note_text))
        row = cur.fetchone()
    return {"note_text": row["note_text"], "updated_at": row["updated_at"].isoformat()}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="static", html=True), name="static")
