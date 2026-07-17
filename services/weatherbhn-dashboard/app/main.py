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

from . import db, fees

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
        cur.execute("""
            SELECT DISTINCT ON (bucket_label)
                bucket_label, bucket_type, bucket_floor, bucket_cap,
                yes_bid, yes_ask, no_bid, no_ask, yes_mid, last_price,
                volume, open_interest, market_ticker, market_status, retrieved_at
            FROM weather_bronze_kalshi_market_snapshots
            WHERE station_code = %s AND target_date = %s AND contract_side = 'high'
            ORDER BY bucket_label, retrieved_at DESC
        """, (station, target_date))
        bucket_rows = cur.fetchall()

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

    sigma_markers = []
    if mu is not None and sigma is not None:
        for n in SIGMA_MARKERS:
            sigma_markers.append({"n": n, "temp_f": round(mu + n * sigma, 1)})

    buckets = []
    for b in sorted(bucket_rows, key=_bucket_sort_key):
        floor = float(b["bucket_floor"]) if b["bucket_floor"] is not None else None
        cap = float(b["bucket_cap"]) if b["bucket_cap"] is not None else None
        volume = float(b["volume"]) if b["volume"] is not None else None
        markers_in_bucket = []
        for m in sigma_markers:
            t = m["temp_f"]
            lo = floor if floor is not None else float("-inf")
            hi = cap if cap is not None else float("inf")
            if lo <= t <= hi:
                markers_in_bucket.append(m["n"])
        buckets.append({
            "bucket_label":  b["bucket_label"],
            "bucket_type":   b["bucket_type"],
            "bucket_floor":  floor,
            "bucket_cap":    cap,
            "yes_bid_cents": round(float(b["yes_bid"]) * 100, 1) if b["yes_bid"] is not None else None,
            "yes_ask_cents": round(float(b["yes_ask"]) * 100, 1) if b["yes_ask"] is not None else None,
            "no_bid_cents":  round(float(b["no_bid"]) * 100, 1) if b["no_bid"] is not None else None,
            "no_ask_cents":  round(float(b["no_ask"]) * 100, 1) if b["no_ask"] is not None else None,
            "chance_pct":    _chance_pct(b),
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
# Static frontend
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="static", html=True), name="static")
