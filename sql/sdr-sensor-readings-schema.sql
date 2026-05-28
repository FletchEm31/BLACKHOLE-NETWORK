-- sdr_sensor_readings
--
-- Decoded telemetry from consumer/industrial RF sensors picked up by
-- `rtl_433`. Covers a long tail of ISM-band devices: wireless weather
-- stations, tire pressure monitors, energy meters, smoke detectors,
-- door sensors, soil moisture probes, etc. rtl_433 ships with ~200
-- decoders that turn the RF bursts into JSON.
--
-- Ingest path TBD — `rtl_433 -F json` on the receiver, tailed by an n8n
-- cron or a small Python collector that INSERTs each decoded record.
--
-- Per-device decoder schemas vary wildly, so we lean on jsonb to keep
-- the data without per-protocol tables. Common fields are promoted to
-- columns so they're cheap to index.

CREATE TABLE IF NOT EXISTS public.sdr_sensor_readings (
    id              bigserial   PRIMARY KEY,
    model           text        NOT NULL,        -- rtl_433 decoder name (e.g. "Acurite-Tower")
    sensor_id       text        NULL,            -- device serial / id (string because varies by protocol)
    channel         text        NULL,            -- channel selector (some devices have A/B/C)
    frequency_mhz   real        NULL,            -- center frequency of the burst (315, 433.92, 868, 915 typical)
    rssi_db         real        NULL,            -- signal strength when received
    raw_json        jsonb       NOT NULL,        -- full rtl_433 record as emitted
    observed_at     timestamptz NOT NULL,
    observer_node   text        NOT NULL DEFAULT 'la'
);

CREATE INDEX IF NOT EXISTS idx_sdr_sensor_readings_observed_at
    ON public.sdr_sensor_readings USING brin (observed_at);

CREATE INDEX IF NOT EXISTS idx_sdr_sensor_readings_model_observed
    ON public.sdr_sensor_readings (model, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_sdr_sensor_readings_sensor_id_observed
    ON public.sdr_sensor_readings (sensor_id, observed_at DESC)
    WHERE sensor_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sdr_sensor_readings_raw_json_gin
    ON public.sdr_sensor_readings USING gin (raw_json);

-- Owner stays `postgres` to match the convention of every other table.

GRANT SELECT, INSERT ON public.sdr_sensor_readings TO bootstrap_writer;
GRANT USAGE, SELECT ON SEQUENCE public.sdr_sensor_readings_id_seq TO bootstrap_writer;

GRANT SELECT ON public.sdr_sensor_readings TO grafana_reader;

COMMENT ON TABLE public.sdr_sensor_readings IS
    'rtl_433 decoded ISM-band telemetry (315/433/868/915 MHz). Long-tail consumer sensors. See infrastructure/docs/sdr-pipeline.md (TBD).';
