#!/usr/bin/env python3
"""
bhn-tcgtracking-pull.py
Pulls all three TCGTracking.com endpoints for every set across 5 games
and stores raw JSON into eventhorizon.

Games:
  Magic         game_id=1   ~444 sets
  YuGiOh        game_id=2   ~612 sets
  Pokemon       game_id=3   ~216 sets
  One Piece     game_id=68  ~ 76 sets
  Pokemon Japan game_id=85  ~216 sets

Endpoints per set:
  /tcgapi/v1/{game_id}/sets/{set_id}          → tcgtracking_{game}_products
  /tcgapi/v1/{game_id}/sets/{set_id}/pricing  → tcgtracking_{game}_pricing
  /tcgapi/v1/{game_id}/sets/{set_id}/skus     → tcgtracking_{game}_skus

Usage:
  # All games (default)
  python3 bhn-tcgtracking-pull.py

  # Single game only
  python3 bhn-tcgtracking-pull.py --game pokemon
  python3 bhn-tcgtracking-pull.py --game magic
  python3 bhn-tcgtracking-pull.py --game yugioh
  python3 bhn-tcgtracking-pull.py --game onepiece
  python3 bhn-tcgtracking-pull.py --game pokemon_japan

Run on LA. Install deps first:
  pip3 install psycopg2-binary requests --break-system-packages

Estimated runtime at 0.5s delay between requests:
  Pokemon       ~  5 min  (216 sets x 3 = 648 requests)
  One Piece     ~  2 min  ( 76 sets x 3 = 228 requests)
  Pokemon Japan ~  5 min  (~216 sets x 3)
  YuGiOh        ~ 15 min  (612 sets x 3 = 1,836 requests)
  Magic         ~ 11 min  (444 sets x 3 = 1,332 requests)
  ALL GAMES     ~ 40 min  total
"""

import os
import sys
import time
import json
import logging
import argparse
import requests
import psycopg2
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# Game registry
# ─────────────────────────────────────────────────────────────────────────────
GAMES = {
    "magic":         {"game_id": 1,  "table_prefix": "tcgtracking_magic"},
    "yugioh":        {"game_id": 2,  "table_prefix": "tcgtracking_yugioh"},
    "pokemon":       {"game_id": 3,  "table_prefix": "tcgtracking_pokemon"},
    "onepiece":      {"game_id": 68, "table_prefix": "tcgtracking_onepiece"},
    "pokemon_japan": {"game_id": 85, "table_prefix": "tcgtracking_pokemon_japan"},
}

BASE_URL  = "https://tcgtracking.com/tcgapi/v1"
SLEEP_SEC = 0.5

DB_DSN = os.environ.get(
    "BHN_PG_DSN",
    "postgresql://ehuser:CHANGE_ME@localhost:5432/eventhorizon"
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("tcgtracking-pull")

# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "Accept":     "application/json",
    "User-Agent": "BHN-DataPipeline/1.0"
})

def fetch(url: str) -> dict | None:
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"FAIL  {url}  → {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────────────────────────────────────
def upsert(cur, table: str, set_id: int, data: dict):
    # `fetched_date` is a STORED generated column = (fetched_at AT TIME ZONE 'UTC')::DATE
    # PK is (set_id, fetched_date) so we conflict on the column, not on an expression.
    cur.execute(f"""
        INSERT INTO {table} (set_id, raw_data, fetched_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (set_id, fetched_date)
        DO UPDATE SET raw_data   = EXCLUDED.raw_data,
                      fetched_at = EXCLUDED.fetched_at
    """, (set_id, json.dumps(data)))

# ─────────────────────────────────────────────────────────────────────────────
# Pull one game
# ─────────────────────────────────────────────────────────────────────────────
def pull_game(conn, game_key: str):
    cfg      = GAMES[game_key]
    game_id  = cfg["game_id"]
    prefix   = cfg["table_prefix"]

    log.info(f"{'='*55}")
    log.info(f"GAME: {game_key.upper()}  (game_id={game_id})")
    log.info(f"{'='*55}")

    index = fetch(f"{BASE_URL}/{game_id}/sets")
    if not index:
        log.error(f"Could not fetch set list for {game_key}. Skipping.")
        return 0, 0, 0

    sets = index.get("sets", [])
    log.info(f"Found {len(sets)} sets.")

    cur = conn.cursor()
    ok = err = skip = 0

    for i, s in enumerate(sets):
        set_id   = s["id"]
        set_name = s.get("name", "?")
        log.info(f"  [{i+1:3}/{len(sets)}]  set {set_id:5}  {set_name}")

        endpoints = [
            (f"{BASE_URL}/{game_id}/sets/{set_id}",         f"{prefix}_products"),
            (f"{BASE_URL}/{game_id}/sets/{set_id}/pricing", f"{prefix}_pricing"),
            (f"{BASE_URL}/{game_id}/sets/{set_id}/skus",    f"{prefix}_skus"),
        ]

        for url, table in endpoints:
            data = fetch(url)
            time.sleep(SLEEP_SEC)

            if data is None:
                log.warning(f"    SKIP  {table}")
                skip += 1
                continue

            try:
                upsert(cur, table, set_id, data)
            except Exception as e:
                log.error(f"    DB ERR  {table}  → {e}")
                conn.rollback()
                err += 1
                continue

        conn.commit()
        ok += 1

    cur.close()
    log.info(f"  {game_key}: sets={len(sets)}  ok={ok}  skip={skip}  err={err}")
    return ok, skip, err

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="TCGTracking raw data puller")
    parser.add_argument(
        "--game",
        choices=list(GAMES.keys()) + ["all"],
        default="all",
        help="Game to pull (default: all)"
    )
    args = parser.parse_args()

    games_to_run = list(GAMES.keys()) if args.game == "all" else [args.game]

    log.info(f"Games to pull: {games_to_run}")
    log.info(f"Connecting to DB...")

    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False

    total_ok = total_skip = total_err = 0
    start = time.time()

    for game_key in games_to_run:
        ok, skip, err = pull_game(conn, game_key)
        total_ok   += ok
        total_skip += skip
        total_err  += err

    conn.close()

    elapsed = int(time.time() - start)
    log.info(f"{'='*55}")
    log.info(f"ALL DONE  {elapsed//60}m {elapsed%60}s")
    log.info(f"Total sets ok={total_ok}  skip={total_skip}  err={total_err}")

if __name__ == "__main__":
    main()
