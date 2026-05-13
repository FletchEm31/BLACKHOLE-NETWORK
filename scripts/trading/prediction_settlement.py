#!/usr/bin/env python3
"""
prediction_settlement.py — BHN Strategy 9 (BHN-PREDICTION-ALPHA) settlement.

Resolves open weather_bets after their contract's resolution_date has passed.
Three-way reconciliation:

  1. Kalshi  — fetch resolved market status; the outcome they settled to
               is the official payout determinant for our bet
  2. NWS CLI — fetch the official NWS Daily Climate Report for the
               (station, target_date); this is the source Kalshi
               themselves use to settle, so a NWS CLI mismatch with
               Kalshi's outcome is a Kalshi data-integrity flag
  3. ASOS    — already in weather_observations from the collector; used
               as a tertiary cross-check (NWS CLI is authoritative for
               settlement; ASOS is the raw observation)

For each settleable bet:
  - UPDATE weather_bets: status (won/lost/voided), exit_at, payout_usd, pnl_usd
  - UPDATE prediction_contracts: resolved_at, resolved_outcome
  - INSERT weather_observations row from NWS CLI (source='nws_cli')
  - LOG any disagreement between Kalshi outcome + NWS CLI observation
    (this should be ~0% in practice; non-zero = Kalshi using bad data
    or our parser misinterpreting the contract)

Payout math (Kalshi binary contracts):
  Each contract pays $1 if YES resolves true; $0 otherwise (mirror for NO).
  count       = stake_usd / entry_price
  payout_usd  = count × $1            if our side wins
              = $0                     otherwise
  pnl_usd     = payout_usd - stake_usd

CLI:
  python3 prediction_settlement.py settle               # process every open
                                                          # bet whose contract
                                                          # has passed resolution
  python3 prediction_settlement.py status               # report counts by status
  python3 prediction_settlement.py fetch-cli KNYC 2026-05-15
                                                        # pull one Daily Climate
                                                        # Report; useful for
                                                        # debugging Kalshi
                                                        # disagreements

No external SDK. NWS CLI parsing written from scratch via regex + the
known fixed-column NWS CLI product text format. Math + reconciliation
logic ours.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests

import trading_core as tc
import kalshi_client as kc


logger = tc.get_logger("strat_9_prediction_alpha_settlement")


# ─────────────────────────────────────────────────────────────────────────
# NWS CLI (Climate Local I) — Daily Climate Report
# ─────────────────────────────────────────────────────────────────────────

NWS_API_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "BHN-Prediction-Settlement/1.0 (operator@eventhorizonvpn.com)"

# NWS CLI products are returned as fixed-format text. Two values matter for
# Kalshi-settled markets:
#   HIGHEST  85    ← max temperature, °F (left-padded to 4-char field)
#   LOWEST   60    ← min temperature
#   PRECIPITATION 0.04   ← inches; 'T' for trace
# Snowfall is also reported when applicable; we don't parse it Phase 1
# (no Kalshi snow markets currently in the 4-city universe).
_RE_TMAX  = re.compile(r"\bMAXIMUM\s+([0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)
_RE_TMIN  = re.compile(r"\bMINIMUM\s+([0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)
_RE_PRCP  = re.compile(r"\bPRECIPITATION\s+(T|[0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)


@dataclass
class NWSCLIReport:
    station_code:   str
    target_date:    date
    tmax_f:         Optional[float]
    tmin_f:         Optional[float]
    precip_in:      Optional[float]   # 0.0 if trace
    raw_text:       str
    product_id:     str
    issued_at:      Optional[datetime]


def _http_get(url: str, accept: str = "application/json",
               timeout: int = 20, attempts: int = 3) -> Optional[requests.Response]:
    """GET with retries + UA header. NWS API requires a User-Agent."""
    for attempt in range(attempts):
        try:
            resp = requests.get(url, timeout=timeout,
                                headers={"User-Agent": NWS_USER_AGENT,
                                          "Accept": accept})
            if resp.status_code == 429:
                wait = 2 ** attempt + 2
                logger.warning(f"NWS 429 on {url}; sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"NWS fetch attempt {attempt+1}/{attempts} on {url}: "
                            f"{e}; sleeping {wait}s")
            time.sleep(wait)
    return None


def fetch_nws_cli(station_code: str, target_date: date) -> Optional[NWSCLIReport]:
    """Two-step fetch:
      1. GET /products/types/CLI/locations/{station} → list of recent products
      2. For each product whose issuance date is ≥ target_date, fetch its
         text body and parse for TMAX/TMIN/PRECIP. The first product whose
         body references target_date wins.

    Kalshi typically settles within 24h of the calendar day's NWS CLI
    publish (usually next morning). We look back up to 4 days to handle
    weekends/holidays where CLI publication is delayed.
    """
    station = station_code.upper()
    # NWS API uses 3-letter codes for most stations; strip leading K
    nws_station = station[1:] if station.startswith("K") else station
    list_url = f"{NWS_API_BASE}/products/types/CLI/locations/{nws_station}"
    resp = _http_get(list_url)
    if not resp:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    products = body.get("@graph") or body.get("graph") or []
    if not products:
        logger.info(f"fetch_nws_cli: no CLI products listed for {station}")
        return None

    # Sort by issuanceTime DESC and walk
    def _issuance(p: dict) -> str:
        return p.get("issuanceTime") or p.get("issuanceTimestamp") or ""
    products.sort(key=_issuance, reverse=True)

    for product in products[:8]:
        product_id = product.get("id") or product.get("productCode") or ""
        text_url = (product.get("@id") or product.get("id") or "").replace(
            "/products/", "/products/")  # already-correct format usually
        # The /products/types/CLI/locations endpoint returns items with @id
        # pointing at /products/{uuid}; fetching that returns the text body.
        prod_url = product.get("@id") or product.get("id")
        if not prod_url:
            continue
        text_resp = _http_get(prod_url)
        if not text_resp:
            continue
        try:
            text_body = text_resp.json().get("productText", "")
        except ValueError:
            text_body = text_resp.text
        if not text_body:
            continue
        # The CLI text references the target_date in MMDD or other formats.
        # We do a sloppy substring check on YYYY-MM-DD and the date number.
        date_strs = (target_date.isoformat(),
                     target_date.strftime("%B %d"),
                     target_date.strftime("%b %d"))
        if not any(s.upper() in text_body.upper() for s in date_strs):
            continue
        report = _parse_cli_text(station, target_date, text_body,
                                  product_id=product_id,
                                  issued_at=_parse_iso8601(_issuance(product)))
        if report is not None:
            return report
    logger.info(f"fetch_nws_cli: no CLI product for {station} {target_date}")
    return None


def _parse_iso8601(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _parse_cli_text(station: str, target_date: date, text: str,
                     product_id: str, issued_at: Optional[datetime]) -> NWSCLIReport:
    """Parse TMAX/TMIN/PRECIP from a CLI product body. Conservative — if a
    field's regex doesn't match, leave it None rather than guess."""
    tmax = tmin = precip = None
    m = _RE_TMAX.search(text)
    if m:
        try:
            tmax = float(m.group(1))
        except ValueError:
            pass
    m = _RE_TMIN.search(text)
    if m:
        try:
            tmin = float(m.group(1))
        except ValueError:
            pass
    m = _RE_PRCP.search(text)
    if m:
        raw = m.group(1).upper()
        if raw == "T":
            precip = 0.0  # trace
        else:
            try:
                precip = float(raw)
            except ValueError:
                pass
    return NWSCLIReport(
        station_code=station, target_date=target_date,
        tmax_f=tmax, tmin_f=tmin, precip_in=precip,
        raw_text=text, product_id=product_id, issued_at=issued_at,
    )


def insert_nws_cli_observations(report: NWSCLIReport) -> int:
    """Write tmax/tmin/precip into weather_observations with source='nws_cli'.
    Idempotent via the UNIQUE (station, observed_at, variable, source)
    constraint. Returns count of new rows."""
    if not report:
        return 0
    observed_at = datetime.combine(
        report.target_date, datetime.min.time(), tzinfo=timezone.utc,
    ).replace(hour=23, minute=59)
    inserted = 0
    samples = (
        ("tmax_f",   report.tmax_f),
        ("tmin_f",   report.tmin_f),
        ("precip_in", report.precip_in),
    )
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                for var, val in samples:
                    if val is None:
                        continue
                    cur.execute("""
                        INSERT INTO weather_observations
                            (station_code, variable, observed_value,
                             observed_at, source, raw_payload)
                        VALUES (%s, %s, %s, %s, 'nws_cli', %s::jsonb)
                        ON CONFLICT (station_code, observed_at, variable, source)
                        DO NOTHING
                    """, (report.station_code, var, float(val),
                          observed_at, json.dumps({
                              "product_id": report.product_id,
                              "issued_at":  (report.issued_at.isoformat()
                                              if report.issued_at else None),
                          })))
                    if cur.rowcount > 0:
                        inserted += 1
    except Exception as e:
        logger.warning(f"insert_nws_cli_observations failed: {e}")
    return inserted


# ─────────────────────────────────────────────────────────────────────────
# Bet settlement
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class SettlementResult:
    bet_id:             int
    ticker:             str
    side:               str
    stake_usd:          Decimal
    entry_price:        Decimal
    kalshi_outcome:     Optional[str]    # 'yes' | 'no' | 'voided' | None
    nws_outcome:        Optional[str]    # derived from NWS CLI + contract metadata
    final_status:       str               # 'won' | 'lost' | 'voided' | 'pending'
    payout_usd:         Decimal
    pnl_usd:            Decimal
    discrepancy:        bool              # Kalshi vs NWS disagreed
    notes:              str


def _settleable_bets() -> list[dict]:
    """Return open weather_bets whose contract's resolution_date has passed."""
    rows: list[dict] = []
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT b.id, b.contract_id, b.side, b.stake_usd,
                           b.entry_price, b.exchange_order_id,
                           c.contract_id AS exchange_ticker, c.title,
                           c.station_code, c.variable,
                           c.resolution_date, c.raw_payload AS contract_raw
                    FROM weather_bets b
                    JOIN prediction_contracts c ON c.id = b.contract_id
                    WHERE b.status = 'open'
                      AND b.exchange = 'kalshi'
                      AND c.resolution_date IS NOT NULL
                      AND c.resolution_date < CURRENT_DATE
                    ORDER BY c.resolution_date ASC
                """)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"_settleable_bets failed: {e}")
    return rows


def _fetch_kalshi_market_outcome(client: kc.KalshiClient,
                                   ticker: str) -> tuple[Optional[str], Optional[dict]]:
    """Return ('yes'|'no'|'voided'|None, raw_market_body). None when the
    market is still open or not found."""
    try:
        body = client.get_market(ticker)
    except kc.KalshiAPIError as e:
        logger.warning(f"Kalshi get_market({ticker}) failed: {e}")
        return None, None
    market = body.get("market") or body
    status = (market.get("status") or "").lower()
    # Kalshi's status field: 'active' | 'closed' | 'settled' | 'finalized'
    if status not in ("settled", "finalized"):
        return None, market
    # Resolved outcome — Kalshi uses 'result' or 'outcome'
    result = (market.get("result") or market.get("outcome") or "").lower()
    if result in ("yes", "true"):
        return "yes", market
    if result in ("no", "false"):
        return "no", market
    if result in ("void", "voided", "canceled"):
        return "voided", market
    return None, market


def _derive_nws_outcome(report: NWSCLIReport,
                         variable: str,
                         threshold: float,
                         threshold_op: str,
                         threshold_high: Optional[float] = None) -> Optional[str]:
    """Map NWS CLI observation → contract resolution. Returns 'yes' | 'no'
    | None (when the relevant field is missing from CLI)."""
    obs_val: Optional[float] = None
    if variable == "tmax_f":
        obs_val = report.tmax_f
    elif variable == "tmin_f":
        obs_val = report.tmin_f
    elif variable == "precip_in":
        obs_val = report.precip_in
    if obs_val is None:
        return None
    if threshold_op == ">":
        return "yes" if obs_val > threshold else "no"
    if threshold_op == ">=":
        return "yes" if obs_val >= threshold else "no"
    if threshold_op == "<":
        return "yes" if obs_val < threshold else "no"
    if threshold_op == "<=":
        return "yes" if obs_val <= threshold else "no"
    if threshold_op == "between" and threshold_high is not None:
        return "yes" if (threshold <= obs_val < threshold_high) else "no"
    return None


def _compute_payout(side: str, stake_usd: Decimal, entry_price: Decimal,
                     kalshi_outcome: str) -> tuple[str, Decimal, Decimal]:
    """Returns (final_status, payout_usd, pnl_usd).

    Kalshi binary payouts:
      - Each contract pays $1 if YES resolves true; $0 otherwise.
      - count = stake_usd / entry_price  (entry_price is fractional 0-1)
      - For 'yes' bet: payout = count × $1 if outcome=='yes' else $0
      - For 'no' bet:  payout = count × $1 if outcome=='no'  else $0
    """
    if kalshi_outcome == "voided":
        # Stake refunded; pnl = 0
        return "voided", stake_usd, Decimal("0")
    if entry_price <= 0:
        return "voided", stake_usd, Decimal("0")
    count = stake_usd / entry_price
    won = (side == kalshi_outcome)
    if won:
        payout = count * Decimal("1.00")
        pnl = payout - stake_usd
        return "won", payout.quantize(Decimal("0.01")), pnl.quantize(Decimal("0.01"))
    payout = Decimal("0")
    pnl = -stake_usd
    return "lost", payout, pnl.quantize(Decimal("0.01"))


def _update_bet(bet_id: int, result: SettlementResult) -> None:
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE weather_bets
                    SET status     = %s,
                        exit_at    = NOW(),
                        payout_usd = %s,
                        pnl_usd    = %s,
                        raw_payload = COALESCE(raw_payload, '{}'::jsonb) ||
                                       jsonb_build_object('settlement',
                                       %s::jsonb)
                    WHERE id = %s
                """, (
                    result.final_status,
                    str(result.payout_usd), str(result.pnl_usd),
                    json.dumps({
                        "kalshi_outcome": result.kalshi_outcome,
                        "nws_outcome":    result.nws_outcome,
                        "discrepancy":    result.discrepancy,
                        "notes":          result.notes,
                        "settled_at":     datetime.now(timezone.utc).isoformat(),
                    }),
                    bet_id,
                ))
    except Exception as e:
        logger.error(f"_update_bet({bet_id}) failed: {e}")


def _update_contract_resolution(contract_db_id: int,
                                  resolved_outcome: Optional[bool]) -> None:
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE prediction_contracts
                    SET resolved_at      = NOW(),
                        resolved_outcome = %s,
                        is_active        = false
                    WHERE id = %s
                      AND resolved_at IS NULL
                """, (resolved_outcome, contract_db_id))
    except Exception as e:
        logger.warning(f"_update_contract_resolution({contract_db_id}) failed: {e}")


def settle_one_bet(client: kc.KalshiClient, bet: dict) -> SettlementResult:
    """Reconcile a single open bet against Kalshi + NWS CLI. Updates PG."""
    from prediction_signal import parse_kalshi_weather_ticker

    ticker = bet["exchange_ticker"]
    side = bet["side"]
    stake = Decimal(str(bet["stake_usd"]))
    entry = Decimal(str(bet["entry_price"]))

    # Kalshi side
    kalshi_outcome, kalshi_market = _fetch_kalshi_market_outcome(client, ticker)

    # NWS CLI side — fetch + insert as observation + derive outcome
    meta = parse_kalshi_weather_ticker(ticker)
    nws_outcome = None
    cli_report = None
    if meta is not None:
        cli_report = fetch_nws_cli(meta.station, meta.target_date)
        if cli_report:
            insert_nws_cli_observations(cli_report)
            nws_outcome = _derive_nws_outcome(
                cli_report, meta.variable,
                meta.threshold, meta.threshold_op, meta.threshold_high,
            )

    notes_parts: list[str] = []
    discrepancy = False

    # Final status decision
    if kalshi_outcome is None:
        # Kalshi hasn't settled yet — leave the bet open, no update
        return SettlementResult(
            bet_id=bet["id"], ticker=ticker, side=side,
            stake_usd=stake, entry_price=entry,
            kalshi_outcome=None, nws_outcome=nws_outcome,
            final_status="pending",
            payout_usd=Decimal("0"), pnl_usd=Decimal("0"),
            discrepancy=False,
            notes=("Kalshi market not yet settled; "
                   f"NWS CLI says {nws_outcome or 'n/a'}"),
        )

    if nws_outcome is not None and nws_outcome != kalshi_outcome \
       and kalshi_outcome != "voided":
        discrepancy = True
        notes_parts.append(
            f"DISCREPANCY: Kalshi={kalshi_outcome} but NWS CLI says "
            f"{nws_outcome}. Operator review."
        )

    final_status, payout, pnl = _compute_payout(side, stake, entry, kalshi_outcome)
    if not notes_parts:
        notes_parts.append(
            f"Kalshi={kalshi_outcome}, NWS={nws_outcome or 'n/a'}, "
            f"count={stake/entry:.2f}"
        )

    result = SettlementResult(
        bet_id=bet["id"], ticker=ticker, side=side,
        stake_usd=stake, entry_price=entry,
        kalshi_outcome=kalshi_outcome, nws_outcome=nws_outcome,
        final_status=final_status, payout_usd=payout, pnl_usd=pnl,
        discrepancy=discrepancy,
        notes=" | ".join(notes_parts),
    )
    _update_bet(bet["id"], result)
    if kalshi_outcome in ("yes", "no"):
        _update_contract_resolution(bet["contract_id"],
                                      resolved_outcome=(kalshi_outcome == "yes"))
    elif kalshi_outcome == "voided":
        _update_contract_resolution(bet["contract_id"], resolved_outcome=None)

    if discrepancy:
        _alert_horizon_discrepancy(result)
    elif final_status in ("won", "lost"):
        _alert_horizon_settle(result)

    return result


def _alert_horizon_settle(result: SettlementResult) -> None:
    try:
        emoji = "✅" if result.final_status == "won" else "❌"
        msg = (
            f"{emoji} Bet settled: {result.ticker} {result.side.upper()}\n"
            f"  outcome: {result.kalshi_outcome.upper()}\n"
            f"  stake: ${result.stake_usd}  payout: ${result.payout_usd}\n"
            f"  P&L: ${result.pnl_usd}"
        )
        tc._send_alert(severity="info" if result.final_status == "won" else "warning",
                       message=msg)
    except Exception:
        pass


def _alert_horizon_discrepancy(result: SettlementResult) -> None:
    try:
        msg = (
            f"⚠️  BHN settlement DISCREPANCY on {result.ticker}\n"
            f"  Kalshi outcome: {result.kalshi_outcome}\n"
            f"  NWS CLI says:   {result.nws_outcome}\n"
            f"  P&L applied (Kalshi-authoritative): ${result.pnl_usd}\n"
            f"  Operator review the contract title vs NWS data."
        )
        tc._send_alert(severity="warning", message=msg)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────

def settle_all_open_bets(client: Optional[kc.KalshiClient] = None) -> dict:
    """Process every settleable open bet. Returns summary dict."""
    if client is None:
        client = kc.KalshiClient(paper_only=True)
    bets = _settleable_bets()
    if not bets:
        logger.info("settle_all_open_bets: no settleable bets")
        return {"checked": 0, "settled": 0, "pending": 0,
                "discrepancies": 0, "results": []}

    settled = pending = discrepancies = 0
    results: list[SettlementResult] = []
    for bet in bets:
        try:
            res = settle_one_bet(client, bet)
        except Exception:
            logger.exception(f"settle_one_bet({bet['id']}) failed; continuing")
            continue
        results.append(res)
        if res.final_status == "pending":
            pending += 1
        elif res.final_status in ("won", "lost", "voided"):
            settled += 1
        if res.discrepancy:
            discrepancies += 1

    logger.info(f"settle_all_open_bets: checked={len(bets)} settled={settled} "
                f"pending={pending} discrepancies={discrepancies}")
    return {
        "checked": len(bets), "settled": settled, "pending": pending,
        "discrepancies": discrepancies,
        "results": [r.__dict__ for r in results],
    }


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def _cli_settle(args) -> int:
    summary = settle_all_open_bets()
    print(f"checked       {summary['checked']}")
    print(f"settled       {summary['settled']}")
    print(f"pending       {summary['pending']}")
    print(f"discrepancies {summary['discrepancies']}")
    for r in summary["results"]:
        if not isinstance(r, dict):
            continue
        print(f"  [{r['bet_id']}] {r['ticker']:35s} "
              f"side={r['side']:3s}  status={r['final_status']:7s}  "
              f"pnl=${r['pnl_usd']}  notes={r['notes']}")
    return 0


def _cli_status(args) -> int:
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status, COUNT(*), SUM(stake_usd),
                           SUM(payout_usd), SUM(pnl_usd)
                    FROM weather_bets
                    GROUP BY status ORDER BY status
                """)
                rows = cur.fetchall()
    except Exception as e:
        print(f"status query failed: {e}")
        return 1
    print(f"{'status':10s} {'n':>5s} {'stake':>12s} {'payout':>12s} {'pnl':>12s}")
    for st, n, stake, payout, pnl in rows:
        print(f"{st:10s} {n:>5d} ${stake or 0:>11.2f} "
              f"${payout or 0:>11.2f} ${pnl or 0:>11.2f}")
    return 0


def _cli_fetch_cli(args) -> int:
    target = date.fromisoformat(args.target_date)
    report = fetch_nws_cli(args.station, target)
    if report is None:
        print(f"no CLI report found for {args.station} {target}")
        return 1
    print(f"station:    {report.station_code}")
    print(f"date:       {report.target_date}")
    print(f"product_id: {report.product_id}")
    print(f"issued_at:  {report.issued_at}")
    print(f"tmax_f:     {report.tmax_f}")
    print(f"tmin_f:     {report.tmin_f}")
    print(f"precip_in:  {report.precip_in}")
    if args.write:
        n = insert_nws_cli_observations(report)
        print(f"wrote {n} weather_observations rows")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BHN Strat 9 settlement")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("settle", help="Reconcile every settleable open bet")
    sub.add_parser("status", help="Counts + P&L totals by status")

    pf = sub.add_parser("fetch-cli", help="Pull one NWS CLI report")
    pf.add_argument("station", help="e.g. KNYC")
    pf.add_argument("target_date", help="YYYY-MM-DD")
    pf.add_argument("--write", action="store_true",
                    help="also insert into weather_observations")

    args = parser.parse_args()
    if args.cmd == "settle":    return _cli_settle(args)
    if args.cmd == "status":    return _cli_status(args)
    if args.cmd == "fetch-cli": return _cli_fetch_cli(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
