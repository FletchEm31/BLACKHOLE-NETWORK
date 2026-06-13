-- WeatherBHN Phase 3 — Stop-loss monitor support table
-- Per WEATHERBHN_STOP_LOSS_SPEC.md

CREATE TABLE IF NOT EXISTS weather_position_exits (
    id                  BIGSERIAL PRIMARY KEY,
    triggered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_ticker       TEXT NOT NULL,
    city                TEXT NOT NULL,
    contract_side       TEXT NOT NULL,
    bucket_floor        NUMERIC,
    bucket_cap          NUMERIC,
    trigger_type        TEXT NOT NULL,
    entry_price         NUMERIC,
    exit_price          NUMERIC,
    contracts           NUMERIC,
    entry_implied_prob  NUMERIC,
    exit_implied_prob   NUMERIC,
    prob_shift          NUMERIC,
    dollar_loss         NUMERIC,
    forecast_at_entry   NUMERIC,
    forecast_at_exit    NUMERIC,
    forecast_shift_f    NUMERIC,
    exit_order_id       TEXT,
    fill_price          NUMERIC,
    realized_pnl        NUMERIC,
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS wpe_triggered_at_idx ON weather_position_exits (triggered_at DESC);
CREATE INDEX IF NOT EXISTS wpe_ticker_idx        ON weather_position_exits (market_ticker);
CREATE INDEX IF NOT EXISTS wpe_dry_run_idx       ON weather_position_exits (triggered_at DESC)
    WHERE notes = 'DRY_RUN';
