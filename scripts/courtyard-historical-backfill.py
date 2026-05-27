#!/usr/bin/env python3
"""
courtyard-historical-backfill.py — BHN

One-shot historical backfill for Courtyard NFT sales (Polygon) that predate
the COURTYARD-BHN | SALES-COLLECTOR n8n workflow.

WHAT IT DOES
------------
1. Pages through PolygonScan tokennfttx for the Courtyard contract
   (0x251be3a17af4892035c37ebf5890f4a4d889dcad), sorted desc, in pages of 100.
2. For each transfer that's a real sale (from != null_address, to != null_address,
   not a mint), attempts to enrich price + NFT traits via OpenSea events API
   filtered by tx hash. If OpenSea has no record, sold_price stays NULL.
3. Resolves card_id via PG resolve_card_id() function.
4. INSERTs into courtyard_sales with ON CONFLICT (item_id) DO NOTHING — safe
   to re-run, won't duplicate rows landed by the live n8n collector.
5. Tracks progress in /var/lib/bhn-courtyard-backfill/state.json so the script
   can be killed and resumed without re-processing.

RUN FROM LA (psql DSN points at WG tunnel hub):
  POLYGONSCAN_API_KEY=xxx OPENSEA_API_KEY=yyy \\
    python3 scripts/courtyard-historical-backfill.py [--max-pages N]

NULL sold_price rows are still useful for ownership history; a follow-up
enrichment pass can fill them in later.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
import requests

# ─── Constants ──────────────────────────────────────────────────────────────

CONTRACT = "0x251be3a17af4892035c37ebf5890f4a4d889dcad"
NULL_ADDR = "0x0000000000000000000000000000000000000000"

POLYGONSCAN_URL = "https://api.polygonscan.com/api"
OPENSEA_EVENT_BY_TX = "https://api.opensea.io/api/v2/events"  # filter param: ?after=&before=

DEFAULT_DSN = "postgresql://log_shipper:BHN-LogShipper-2026@10.8.0.1/eventhorizon"
DEFAULT_STATE = Path("/var/lib/bhn-courtyard-backfill/state.json")

CANONICAL_SETS = {
    "BASE": "Base Set", "BASE SET": "Base Set", "POKEMON BASE SET": "Base Set",
    "FOSSIL": "Fossil", "POKEMON FOSSIL": "Fossil",
    "JUNGLE": "Jungle", "POKEMON JUNGLE": "Jungle",
    "TEAM ROCKET": "Team Rocket", "POKEMON TEAM ROCKET": "Team Rocket",
    "GYM HEROES": "Gym Heroes", "POKEMON GYM HEROES": "Gym Heroes",
    "GYM CHALLENGE": "Gym Challenge", "POKEMON GYM CHALLENGE": "Gym Challenge",
    "WIZARDS BLACK STAR PROMOS": "Wizards Black Star Promos",
    "BEST OF GAME": "Best of Game",
}


# ─── State persistence ──────────────────────────────────────────────────────

@dataclass
class State:
    last_block: int = 0          # highest block we've processed (we go desc, so this is for resume)
    pages_processed: int = 0
    sales_inserted: int = 0
    sales_enriched: int = 0      # sold_price came from OpenSea
    sales_bare: int = 0          # sold_price stayed NULL
    cursor_page: int = 1         # next PolygonScan page to fetch
    started_at: str = ""

    @classmethod
    def load(cls, path: Path) -> "State":
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls(started_at=datetime.now(timezone.utc).isoformat())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2))


# ─── HTTP helpers ───────────────────────────────────────────────────────────

def polygonscan_page(api_key: str, page: int, offset: int = 100) -> list[dict[str, Any]]:
    """One page of Courtyard transfer events, sorted desc by block."""
    params = {
        "module": "account",
        "action": "tokennfttx",
        "contractaddress": CONTRACT,
        "page": page,
        "offset": offset,
        "sort": "desc",
        "apikey": api_key,
    }
    r = requests.get(POLYGONSCAN_URL, params=params, timeout=30)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "1":
        # "No transactions found" returns status="0", message="No transactions found"
        return []
    return body.get("result", [])


def opensea_event_by_tx(api_key: str, tx_hash: str) -> dict[str, Any] | None:
    """Look up an OpenSea sale event by Polygon tx hash. Returns the event or None."""
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    # OpenSea doesn't have a direct "lookup by tx" endpoint — we filter the events
    # stream. For backfill, this is best-effort: if we get rate-limited or the event
    # is too old, the row still inserts with sold_price = NULL.
    params = {"chain": "matic", "event_type": "sale", "limit": 50}
    try:
        r = requests.get(
            f"{OPENSEA_EVENT_BY_TX}/chain/matic/contract/{CONTRACT}",
            params=params, headers=headers, timeout=20,
        )
        if r.status_code == 429:
            time.sleep(2)
            return None
        r.raise_for_status()
        for ev in r.json().get("asset_events", []):
            if (ev.get("transaction") or "").lower() == tx_hash.lower():
                return ev
    except requests.RequestException:
        return None
    return None


# ─── Normalization (mirrors the n8n Code node) ──────────────────────────────

def find_attr(traits: list[dict[str, Any]], names: list[str]) -> str | None:
    if not traits:
        return None
    by_name = {(t.get("trait_type") or "").lower(): t for t in traits}
    for name in names:
        t = by_name.get(name.lower())
        if t and t.get("value") not in (None, ""):
            return str(t["value"]).strip()
    return None


def normalize_grader(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.upper()
    if "PSA" in u: return "PSA"
    if "CGC" in u: return "CGC"
    if "BGS" in u or "BECKETT" in u: return "BGS"
    if "SGC" in u: return "SGC"
    return None


def normalize_set(raw: str | None) -> str | None:
    if not raw:
        return None
    return CANONICAL_SETS.get(raw.strip().upper())


def normalize_edition(raw: str | None) -> str:
    if not raw:
        return "N/A"
    u = raw.upper()
    if "1ST" in u or "FIRST" in u: return "1st Edition"
    if "SHADOW" in u: return "Shadowless"
    if "UNLIM" in u: return "Unlimited"
    return "N/A"


def normalize_card_number(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).lstrip("#")
    if "/" in s:
        s = s.split("/", 1)[0]
    return s.strip() or None


# ─── Row construction ───────────────────────────────────────────────────────

def build_row(transfer: dict[str, Any], event: dict[str, Any] | None) -> dict[str, Any]:
    """Combine a PolygonScan transfer + (optional) OpenSea event into a courtyard_sales row."""
    item_id = transfer.get("tokenID")
    tx_hash = transfer.get("hash")
    seller = transfer.get("from")
    buyer = transfer.get("to")
    block_time = int(transfer.get("timeStamp", "0"))
    iso_time = datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None

    row = {
        "item_id": str(item_id) if item_id is not None else None,
        "title": None,
        "card_name": None,
        "grader": None,
        "grade": None,
        "set_name": None,
        "edition": "N/A",
        "print_variant": "Standard",
        "card_number": None,
        "sold_price": None,
        "transaction_hash": tx_hash,
        "seller_address": seller,
        "buyer_address": buyer,
        "seller_username": seller,
        "listing_url": None,
        "image_url": None,
        "item_creation_date": iso_time,
        "raw_payload": {"polygonscan": transfer},
    }

    if event:
        nft = event.get("nft") or {}
        traits = nft.get("traits") or []
        payment = event.get("payment") or {}

        grader = normalize_grader(find_attr(traits, ["Grading Company", "Grader", "Grading", "Authenticator"]))
        if grader:
            row["grader"] = grader
        row["title"] = nft.get("name")
        row["card_name"] = find_attr(traits, ["Card Name", "Name", "Card"]) or nft.get("name")
        row["grade"] = find_attr(traits, ["Grade", "Card Grade"])
        row["set_name"] = normalize_set(find_attr(traits, ["Set Name", "Set", "Series"]))
        row["edition"] = normalize_edition(find_attr(traits, ["Edition", "Print Edition"]))
        row["card_number"] = normalize_card_number(find_attr(traits, ["Card Number", "#", "Number"]))
        row["listing_url"] = nft.get("opensea_url") or nft.get("permalink")
        row["image_url"] = nft.get("image_url") or nft.get("display_image_url")
        if payment.get("quantity") and payment.get("decimals") is not None:
            row["sold_price"] = float(payment["quantity"]) / (10 ** int(payment["decimals"]))
        row["raw_payload"]["opensea"] = event

    return row


# ─── DB insert ──────────────────────────────────────────────────────────────

INSERT_SQL = """
INSERT INTO courtyard_sales (
  item_id, title, card_name, grader, grade, set_name, edition, print_variant,
  card_number, sold_price, listed_price, currency, transaction_type, sale_type,
  transaction_hash, seller_address, buyer_address, seller_username,
  platform, blockchain, nft_contract, item_creation_date, image_url, listing_url,
  raw_payload, card_id
) VALUES (
  %(item_id)s, %(title)s, %(card_name)s, %(grader)s, %(grade)s, %(set_name)s,
  %(edition)s, %(print_variant)s, %(card_number)s, %(sold_price)s, NULL,
  'USDC', 'SALE', 'peer_to_peer',
  %(transaction_hash)s, %(seller_address)s, %(buyer_address)s, %(seller_username)s,
  'courtyard', 'polygon', %(contract)s,
  %(item_creation_date)s, %(image_url)s, %(listing_url)s,
  %(raw_payload)s::jsonb,
  resolve_card_id(%(card_name)s, %(set_name)s, %(card_number)s, %(edition)s, %(print_variant)s)
)
ON CONFLICT (item_id) DO NOTHING
RETURNING id, card_id;
"""


def insert_row(cur: psycopg2.extensions.cursor, row: dict[str, Any]) -> int | None:
    cur.execute(INSERT_SQL, {**row, "contract": CONTRACT, "raw_payload": json.dumps(row["raw_payload"])})
    rec = cur.fetchone()
    return rec[0] if rec else None


# ─── Main ───────────────────────────────────────────────────────────────────

def iter_transfers(api_key: str, start_page: int, max_pages: int | None) -> Iterator[dict[str, Any]]:
    page = start_page
    while True:
        if max_pages is not None and (page - start_page) >= max_pages:
            return
        batch = polygonscan_page(api_key, page)
        if not batch:
            return
        for t in batch:
            yield {**t, "_page": page}
        page += 1
        time.sleep(0.25)  # rate-limit cushion under 5 req/sec free tier


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Cap on PolygonScan pages to fetch this run (default: unlimited).")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE,
                        help=f"State file path (default: {DEFAULT_STATE}).")
    parser.add_argument("--dsn", default=os.environ.get("PG_DSN", DEFAULT_DSN),
                        help="PostgreSQL DSN (default: log_shipper @ LA WG hub).")
    parser.add_argument("--no-enrich", action="store_true",
                        help="Skip OpenSea enrichment — insert bare transfers with sold_price=NULL.")
    args = parser.parse_args()

    polygonscan_key = os.environ.get("POLYGONSCAN_API_KEY")
    opensea_key = os.environ.get("OPENSEA_API_KEY")
    if not polygonscan_key:
        print("ERROR: POLYGONSCAN_API_KEY env var required", file=sys.stderr)
        return 2
    if not opensea_key and not args.no_enrich:
        print("WARN: OPENSEA_API_KEY not set — enrichment will be skipped, sold_price will be NULL",
              file=sys.stderr)
        args.no_enrich = True

    state = State.load(args.state)
    print(f"[backfill] resume from page {state.cursor_page} | "
          f"prior: {state.sales_inserted} inserted ({state.sales_enriched} enriched, {state.sales_bare} bare)")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            for t in iter_transfers(polygonscan_key, state.cursor_page, args.max_pages):
                # Skip mints (from = null address) and burns (to = null address)
                if (t.get("from") or "").lower() == NULL_ADDR:
                    continue
                if (t.get("to") or "").lower() == NULL_ADDR:
                    continue

                event = None
                if not args.no_enrich:
                    event = opensea_event_by_tx(opensea_key, t["hash"])
                    time.sleep(0.3)  # OpenSea rate-limit cushion

                row = build_row(t, event)
                inserted_id = insert_row(cur, row)

                if inserted_id is not None:
                    state.sales_inserted += 1
                    if row["sold_price"] is not None:
                        state.sales_enriched += 1
                    else:
                        state.sales_bare += 1

                # Commit + save state every 50 rows so kill-resume is safe.
                if state.sales_inserted % 50 == 0 and state.sales_inserted > 0:
                    conn.commit()
                    state.save(args.state)
                    print(f"[backfill] {state.sales_inserted} inserted "
                          f"(enriched={state.sales_enriched}, bare={state.sales_bare})")

                state.cursor_page = t["_page"]
                state.last_block = max(state.last_block, int(t.get("blockNumber", 0)))

            conn.commit()
            state.save(args.state)
    finally:
        conn.close()

    print(f"[backfill] done. total: {state.sales_inserted} inserted "
          f"(enriched={state.sales_enriched}, bare={state.sales_bare})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
