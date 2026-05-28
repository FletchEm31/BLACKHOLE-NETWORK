-- sdr_aircraft_positions
--
-- ADS-B aircraft position broadcasts decoded by `dump1090` running on the
-- node that owns the RTL-SDR dongle (initially LA when the hardware arrives
-- 2026-05-29; observer_node tracks which receiver if/when more nodes get
-- dongles).
--
-- Ingest path TBD — likely an n8n cron that tails dump1090's JSON output
-- on the receiver and INSERTs in batches.
--
-- High-volume time-series: each aircraft broadcasts position every 0.5–2s.
-- Expect ~1–10k rows/min during busy periods near major airports. BRIN on
-- observed_at handles the typical "last N hours" query pattern cheaply
-- without an enormous btree.

CREATE TABLE IF NOT EXISTS public.sdr_aircraft_positions (
    id              bigserial   PRIMARY KEY,
    icao24          text        NOT NULL,        -- 24-bit ICAO transponder address (hex, e.g. "a1b2c3")
    callsign        text        NULL,            -- 8-char flight ID if broadcast (e.g. "UAL123")
    lat             double precision NULL,
    lon             double precision NULL,
    altitude_ft     integer     NULL,            -- barometric altitude, feet
    velocity_kt     real        NULL,            -- ground speed, knots
    heading_deg     real        NULL,            -- track over ground, degrees true
    vertical_rate_fpm integer   NULL,            -- climb/descend rate, ft/min
    squawk          text        NULL,            -- mode-A squawk code (4 octal digits)
    on_ground       boolean     NULL,
    observed_at     timestamptz NOT NULL,        -- when dump1090 decoded the message
    observer_node   text        NOT NULL DEFAULT 'la'  -- which receiver heard it
);

-- Time-range scans (Grafana panels, last-N-hours queries) — BRIN is ideal
CREATE INDEX IF NOT EXISTS idx_sdr_aircraft_positions_observed_at
    ON public.sdr_aircraft_positions USING brin (observed_at);

-- Per-aircraft history lookups
CREATE INDEX IF NOT EXISTS idx_sdr_aircraft_positions_icao24_observed
    ON public.sdr_aircraft_positions (icao24, observed_at DESC);

-- Partial index for callsign-based queries (most rows have no callsign)
CREATE INDEX IF NOT EXISTS idx_sdr_aircraft_positions_callsign_observed
    ON public.sdr_aircraft_positions (callsign, observed_at DESC)
    WHERE callsign IS NOT NULL;

-- Owner stays `postgres` to match the convention of every other table in
-- this DB (see `\dt+` — every public.* table is owned by postgres).

GRANT SELECT, INSERT ON public.sdr_aircraft_positions TO bootstrap_writer;
GRANT USAGE, SELECT ON SEQUENCE public.sdr_aircraft_positions_id_seq TO bootstrap_writer;

GRANT SELECT ON public.sdr_aircraft_positions TO grafana_reader;

COMMENT ON TABLE public.sdr_aircraft_positions IS
    'ADS-B aircraft position broadcasts decoded by dump1090. One row per Mode-S/ES message that carried positional data. See infrastructure/docs/sdr-pipeline.md (TBD).';
