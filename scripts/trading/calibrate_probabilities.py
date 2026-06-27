"""
WeatherBHN calibration writer.

Reads the Silver calibration training set, applies per-(station, contract_side)
isotonic calibration, and writes results to weather_gold_calibrated_probabilities.

A2 fix: all inserts use datetime.now(timezone.utc) — never datetime.now().
"""
import os
import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import numpy as np
from sklearn.isotonic import IsotonicRegression

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DSN = os.environ.get("BHN_PG_DSN", "host=/var/run/postgresql dbname=bhn user=ehuser")
MIN_TRAINING_ROWS = 30


def load_training_data(cur, station_code: str, contract_side: str) -> tuple:
    """Return (raw_probs, outcomes) arrays from silver calibration training set."""
    cur.execute(
        """
        SELECT raw_prob, outcome
        FROM weather_silver_calibration_training_set
        WHERE station_code = %s
          AND contract_side = %s
          AND outcome IS NOT NULL
        ORDER BY target_date
        """,
        (station_code, contract_side),
    )
    rows = cur.fetchall()
    if not rows:
        return np.array([]), np.array([])
    raw_probs = np.array([r[0] for r in rows], dtype=float)
    outcomes  = np.array([r[1] for r in rows], dtype=int)
    return raw_probs, outcomes


def fit_isotonic(raw_probs: np.ndarray, outcomes: np.ndarray) -> IsotonicRegression:
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_probs, outcomes)
    return iso


def calibrate_and_write(conn) -> int:
    """Run calibration for all active station/side grains. Returns rows written."""
    written = 0
    now_utc = datetime.now(timezone.utc)  # A2 fix: always UTC-aware

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Fetch active grains from the edge sheet
        cur.execute(
            """
            SELECT DISTINCT station_code, contract_side
            FROM weather_gold_daily_edge_sheet
            WHERE is_active = true
              AND target_date >= CURRENT_DATE
            """
        )
        grains = cur.fetchall()

    for grain in grains:
        station_code  = grain["station_code"]
        contract_side = grain["contract_side"]

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            raw_probs, outcomes = load_training_data(cur, station_code, contract_side)

        if len(raw_probs) < MIN_TRAINING_ROWS:
            log.info(
                "Skipping %s/%s — only %d training rows (need %d)",
                station_code, contract_side, len(raw_probs), MIN_TRAINING_ROWS,
            )
            continue

        iso = fit_isotonic(raw_probs, outcomes)

        # Fetch active contracts for this grain
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT city, station_code, target_date, contract_side,
                       bucket_floor, bucket_cap, market_implied_prob
                FROM weather_gold_daily_edge_sheet
                WHERE station_code = %s
                  AND contract_side = %s
                  AND is_active = true
                  AND target_date >= CURRENT_DATE
                  AND market_implied_prob IS NOT NULL
                """,
                (station_code, contract_side),
            )
            contracts = cur.fetchall()

        with conn.cursor() as cur:
            for c in contracts:
                raw_p         = float(c["market_implied_prob"])
                calibrated_p  = float(iso.predict([raw_p])[0])

                cur.execute(
                    """
                    INSERT INTO weather_gold_calibrated_probabilities
                        (station_code, target_date, contract_side,
                         bucket_floor, bucket_cap,
                         raw_prob, calibrated_prob,
                         calibrator_version, calculated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (station_code, target_date, contract_side,
                                 bucket_floor, bucket_cap)
                    DO UPDATE SET
                        raw_prob          = EXCLUDED.raw_prob,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        calibrator_version= EXCLUDED.calibrator_version,
                        calculated_at     = EXCLUDED.calculated_at
                    """,
                    (
                        c["station_code"],
                        c["target_date"],
                        c["contract_side"],
                        c["bucket_floor"],
                        c["bucket_cap"],
                        raw_p,
                        calibrated_p,
                        "isotonic_v1",
                        now_utc,          # A2 fix: UTC-aware datetime
                    ),
                )
                written += 1

        conn.commit()
        log.info("Calibrated %s/%s → %d contracts", station_code, contract_side, len(contracts))

    return written


def main():
    log.info("Starting calibration run")
    with psycopg2.connect(DSN) as conn:
        n = calibrate_and_write(conn)
    log.info("Calibration complete — %d rows written", n)


if __name__ == "__main__":
    main()
