-- sdr_aprs_packets
--
-- APRS (Automatic Packet Reporting System) packets decoded by `Direwolf`
-- from the 144.39 MHz VHF amateur band. Direwolf consumes audio from the
-- RTL-SDR and emits decoded frames as text on its KISS or AGW socket.
--
-- Ingest path TBD — likely a small Python collector subscribes to
-- Direwolf's socket and INSERTs as packets arrive.
--
-- Volume is much lower than ADS-B: hundreds of packets per hour per
-- urban area. No need for BRIN aggressiveness; a regular btree on
-- observed_at is fine.

CREATE TABLE IF NOT EXISTS public.sdr_aprs_packets (
    id              bigserial   PRIMARY KEY,
    from_call       text        NOT NULL,        -- source callsign (e.g. "N0CALL-9")
    to_call         text        NULL,            -- destination callsign or generic ("APRS", "BEACON")
    path            text[]      NULL,            -- digipeater path (e.g. {"WIDE1-1","WIDE2-1"})
    packet_type     text        NULL,            -- position, message, weather, status, telemetry, etc.
    raw             text        NOT NULL,        -- the verbatim packet as Direwolf reports it
    decoded         jsonb       NULL,            -- parsed fields (lat/lon/comment/symbol/etc.)
    observed_at     timestamptz NOT NULL,
    observer_node   text        NOT NULL DEFAULT 'la'
);

CREATE INDEX IF NOT EXISTS idx_sdr_aprs_packets_observed_at
    ON public.sdr_aprs_packets (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_sdr_aprs_packets_from_call_observed
    ON public.sdr_aprs_packets (from_call, observed_at DESC);

-- GIN index on decoded for jsonb path lookups (e.g. "all packets with weather data")
CREATE INDEX IF NOT EXISTS idx_sdr_aprs_packets_decoded_gin
    ON public.sdr_aprs_packets USING gin (decoded);

-- Owner stays `postgres` to match the convention of every other table.

GRANT SELECT, INSERT ON public.sdr_aprs_packets TO bootstrap_writer;
GRANT USAGE, SELECT ON SEQUENCE public.sdr_aprs_packets_id_seq TO bootstrap_writer;

GRANT SELECT ON public.sdr_aprs_packets TO grafana_reader;

COMMENT ON TABLE public.sdr_aprs_packets IS
    'APRS packets decoded by Direwolf from the 144.39 MHz VHF amateur band. Raw + structured forms preserved. See infrastructure/docs/sdr-pipeline.md (TBD).';
