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


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Exact standard-normal CDF via math.erf (not an approximation --
    unlike the frontend chart's JS Abramowitz-Stegun fallback, used only
    because JS has no built-in erf). x must be a real number or +-inf --
    callers convert None (open-ended bucket edge) to the correct signed
    infinity BEFORE calling this; None means -inf for a floor and +inf for
    a cap, so it can't be handled generically inside this function."""
    if math.isinf(x):
        return 1.0 if x > 0 else 0.0
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


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
        if mu is not None and sigma is not None and sigma > 0:
            cdf_hi = _normal_cdf(cap if cap is not None else float("inf"), mu, sigma)
            cdf_lo = _normal_cdf(floor if floor is not None else float("-inf"), mu, sigma)
            model_prob_pct = round((cdf_hi - cdf_lo) * 100, 1)
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
            "volume":        volume,
            "open_interest": float(b["open_interest"]) if b["open_interest"] is not None else None,
            "is_liquid":     (volume > 100.0) if volume is not None else None,
            "market_ticker": b["market_ticker"],
            "sigma_markers_in_bucket": markers_in_bucket,
            "retrieved_at":  b["retrieved_at"].isoformat() if b["retrieved_at"] else None,
        })

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
