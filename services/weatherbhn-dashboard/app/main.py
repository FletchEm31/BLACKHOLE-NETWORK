"""WeatherBHN Trading Dashboard — FastAPI backend.

Read-only planning/simulation tool. Does NOT place orders, does NOT touch
DRY_RUN/enabled flags, does NOT read or write any CP1-4 decision logic.
Scope: KDEN, KLAX, KMIA (the 3 tradeable cities) -- CITIES below lists all
8 BHN weather stations with an `enabled` flag so adding a future city is a
config change, not a rebuild, per operator scope note.
"""
import math
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
        data_stale = len(bucket_rows) == 0

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

        cur.execute("""
            SELECT final_entry_predicted_tmax_f, final_entry_sigma_used,
                   predicted_tmax_f, sigma_used, decision_timestamp,
                   hours_to_settle
            FROM weather_position_exits_clean
            WHERE station_code = %s AND target_date = %s
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
# "Robust" threshold (n>=8 for green/red, else 'thin') is borrowed directly
# from that doc's own stated bar for calling a zone trustworthy -- not an
# arbitrary number invented here.
# ---------------------------------------------------------------------------

ROBUST_N_THRESHOLD = 8


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
    n = len(rows)
    wins = sum(1 for r in rows if r["win"])
    staked = sum(r["stake_usd"] for r in rows if r["stake_usd"] is not None)
    pnl = sum(r["pnl"] for r in rows if r["pnl"] is not None)
    win_pct = round(wins / n * 100, 1) if n else None
    roi_pct = round(pnl / staked * 100, 1) if staked else None

    if n == 0:
        tag = "no_data"
    elif n < ROBUST_N_THRESHOLD:
        tag = "thin"
    elif roi_pct is not None and roi_pct > 0:
        tag = "green"
    elif roi_pct is not None and roi_pct < 0:
        tag = "red"
    else:
        tag = "thin"

    return {"n": n, "wins": wins, "win_pct": win_pct,
            "staked": round(staked, 2), "pnl": round(pnl, 2),
            "roi_pct": roi_pct, "tag": tag}


def _historical_yes_ask_cents(cur, station_code: str, target_date: date,
                               bucket_label: str, entry_captured_at) -> Optional[float]:
    """Real yes_ask quoted at or before the trade's actual entry moment --
    never derived as (100 - no_ask): confirmed earlier tonight that
    Yes+No don't sum cleanly to 100 cents (averaging ~105c), so that
    shortcut would misprice every simulated Yes trade.

    Bounded to a 2-hour lookback (collector runs ~5 min, so a real quote
    should be well within that) rather than an open-ended <= scan --
    weather_bronze_kalshi_market_snapshots is a 13M+-row table partitioned
    by retrieved_at; an unbounded backward scan measured 20s for ~15
    matched rows. This is a query-shape fix only, no index/schema change on
    the live table."""
    cur.execute("""
        SELECT yes_ask FROM weather_bronze_kalshi_market_snapshots
        WHERE station_code = %s AND target_date = %s AND bucket_label = %s
          AND contract_side = 'high'
          AND retrieved_at <= %s AND retrieved_at >= %s - INTERVAL '2 hours'
        ORDER BY retrieved_at DESC LIMIT 1
    """, (station_code, target_date, bucket_label, entry_captured_at, entry_captured_at))
    row = cur.fetchone()
    return round(float(row["yes_ask"]) * 100, 2) if row and row["yes_ask"] is not None else None


# 0sigma is structurally the worst possible No bet -- it's the bucket the
# model itself thinks is most likely to occur, so a No bet there is
# betting against the model's own best guess. The live system only ever
# bets No, so 0sigma's cell would otherwise just show "confirmed bad" and
# nothing more useful. Per operator direction 2026-07-18: retroactively
# resimulate 0sigma's trades as Yes bets instead, using the REAL yes_ask
# quoted at that trade's actual entry_captured_at (not inverted no_ask --
# see _historical_yes_ask_cents), same dollar amount originally staked,
# win/loss flipped (a No loss -- the bucket occurred -- becomes a Yes win).
#
# Same honest-framing requirement as the earlier standalone 0-1sigma Yes
# analysis: a real edge here is a modest calibration signal, not a
# headline ROI number -- 'yes_simulated' surfaces which cell this applies
# to so the frontend can carry that caveat explicitly, not just imply it.
ZERO_SIGMA_YES_CAVEAT = (
    "0sigma is resimulated as a Yes bet (the live system never trades Yes) "
    "using the real historical yes_ask at each trade's actual entry moment, "
    "same dollar stake, win/loss flipped from the recorded No outcome. "
    "Treat any edge here as a modest win-rate/calibration signal, not a "
    "literal ROI you could have captured -- a small sample easily produces "
    "a misleadingly large headline number."
)


@app.get("/api/sigma-performance")
def get_sigma_performance():
    with db.conn_cursor() as cur:
        cur.execute("""
            SELECT station_code, target_date, bucket_label, bucket_floor, bucket_cap,
                   final_entry_predicted_tmax_f, final_entry_sigma_used,
                   final_outcome, final_realized_pnl_usd,
                   entry_no_ask_cents, entry_captured_at, final_contracts_recommended
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
        zero_sigma_unmatched = 0

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

            if marker == 0 and stake_usd is not None and r["entry_captured_at"] is not None:
                yes_ask_cents = _historical_yes_ask_cents(
                    cur, r["station_code"], r["target_date"], r["bucket_label"], r["entry_captured_at"]
                )
                if yes_ask_cents is None or yes_ask_cents <= 0:
                    zero_sigma_unmatched += 1
                    continue  # no real historical price found -- excluded, not guessed at
                contracts_yes = math.floor(stake_usd / (yes_ask_cents / 100.0))
                cost_yes = contracts_yes * yes_ask_cents / 100.0
                yes_win = r["final_outcome"] == "NO_LOSS"  # bucket occurred -> No lost -> Yes would have won
                pnl_yes = (contracts_yes * 1.0 - cost_yes) if yes_win else -cost_yes
                row = {"win": yes_win, "pnl": pnl_yes, "stake_usd": cost_yes}
            else:
                row = {
                    "win": r["final_outcome"] == "NO_WIN",
                    "pnl": float(r["final_realized_pnl_usd"]) if r["final_realized_pnl_usd"] is not None else None,
                    "stake_usd": stake_usd,
                }
            per_marker[marker].append(row)
            per_city_marker[r["station_code"]][marker].append(row)

    pooled = {str(n): _aggregate_cell(per_marker[n]) for n in SIGMA_MARKERS}
    pooled["0"]["yes_simulated"] = True
    pooled["0"]["yes_simulated_note"] = ZERO_SIGMA_YES_CAVEAT
    pooled["0"]["yes_simulated_unmatched"] = zero_sigma_unmatched

    by_city = {}
    for city in ENABLED_STATIONS:
        by_city[city] = {str(n): _aggregate_cell(per_city_marker[city][n]) for n in SIGMA_MARKERS}
        by_city[city]["0"]["yes_simulated"] = True
        by_city[city]["0"]["yes_simulated_note"] = ZERO_SIGMA_YES_CAVEAT

    return {
        "robust_n_threshold": ROBUST_N_THRESHOLD,
        "markers": SIGMA_MARKERS,
        "pooled": pooled,
        "by_city": by_city,
    }


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
