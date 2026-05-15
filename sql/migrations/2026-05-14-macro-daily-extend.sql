-- 2026-05-14-macro-daily-extend.sql
-- Extends macro_daily with the full Treasury curve (1M–30Y), 15Y/30Y fixed
-- mortgage averages, and gold + silver spot. Adds them to v_ticker_analysis
-- so HORIZON's analyze_ticker tool sees them downstream.
--
-- FRED series mapped:
--   DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS5, DGS7, DGS10, DGS30  (daily)
--   MORTGAGE15US, MORTGAGE30US                                    (weekly)
--   GOLDAMGBD228NLBM                                              (daily, LBMA AM fix)
--   SLVPRUSD                                                      (daily — verify FRED has this id;
--                                                                  if not, swap to LBMA silver series)
--
-- DGS10 is the raw 10Y treasury yield (distinct from the existing
-- yield_curve_10y2y / 10y3m columns which are FRED-computed spreads).
--
-- Apply on LA:
--   sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-05-14-macro-daily-extend.sql
--
-- After apply, re-run the collector with --backfill to populate history:
--   sudo -u bhn-trader /opt/bhn/scripts/horizon/macro_collector.py --backfill

\set ON_ERROR_STOP on

BEGIN;

-- ────────────────────────────────────────────────────────────────────────
-- 1. New columns on macro_daily
-- ────────────────────────────────────────────────────────────────────────
ALTER TABLE macro_daily
    ADD COLUMN IF NOT EXISTS treasury_1m         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_3m         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_6m         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_1y         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_2y         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_5y         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_7y         NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_10y        NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS treasury_30y        NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS mortgage_15y_fixed  NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS mortgage_30y_fixed  NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS gold_spot_usd       NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS silver_spot_usd     NUMERIC(12, 4);

COMMENT ON COLUMN macro_daily.treasury_1m        IS '1-month constant-maturity Treasury yield, percent. FRED DGS1MO.';
COMMENT ON COLUMN macro_daily.treasury_3m        IS '3-month constant-maturity Treasury yield, percent. FRED DGS3MO.';
COMMENT ON COLUMN macro_daily.treasury_6m        IS '6-month constant-maturity Treasury yield, percent. FRED DGS6MO.';
COMMENT ON COLUMN macro_daily.treasury_1y        IS '1-year constant-maturity Treasury yield, percent. FRED DGS1.';
COMMENT ON COLUMN macro_daily.treasury_2y        IS '2-year constant-maturity Treasury yield, percent. FRED DGS2.';
COMMENT ON COLUMN macro_daily.treasury_5y        IS '5-year constant-maturity Treasury yield, percent. FRED DGS5.';
COMMENT ON COLUMN macro_daily.treasury_7y        IS '7-year constant-maturity Treasury yield, percent. FRED DGS7.';
COMMENT ON COLUMN macro_daily.treasury_10y       IS '10-year constant-maturity Treasury yield, percent. FRED DGS10.';
COMMENT ON COLUMN macro_daily.treasury_30y       IS '30-year constant-maturity Treasury yield, percent. FRED DGS30.';
COMMENT ON COLUMN macro_daily.mortgage_15y_fixed IS '15-year fixed-rate mortgage US average, percent. FRED MORTGAGE15US. Weekly — forward-filled to business days.';
COMMENT ON COLUMN macro_daily.mortgage_30y_fixed IS '30-year fixed-rate mortgage US average, percent. FRED MORTGAGE30US. Weekly — forward-filled to business days.';
COMMENT ON COLUMN macro_daily.gold_spot_usd      IS 'Gold spot USD/oz, LBMA AM fix. FRED GOLDAMGBD228NLBM.';
COMMENT ON COLUMN macro_daily.silver_spot_usd    IS 'Silver spot USD/oz. FRED SLVPRUSD (verify availability; LBMA silver fix alt source if 404).';


-- ────────────────────────────────────────────────────────────────────────
-- 2. Refresh v_ticker_analysis to expose the new columns
-- ────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_ticker_analysis AS
SELECT
    md.ticker,
    md.date,
    md.open, md.high, md.low, md.close, md.volume,
    md.sma_20, md.sma_50, md.sma_100, md.sma_200,
    md.rsi_14, md.atr_14,
    md.bb_upper, md.bb_lower, md.bb_width,
    md.roc_9, md.roc_21, md.roc_63,
    md.volume_ratio,
    md.high_52w, md.low_52w, md.pct_from_52w_high,
    mac.vix,
    mac.yield_curve_10y2y,
    mac.yield_curve_10y3m,
    mac.fed_funds_rate,
    mac.cpi,
    mac.unemployment,
    mac.consumer_sentiment,
    mac.high_yield_spread,
    mac.dollar_index,
    mac.treasury_1m,
    mac.treasury_3m,
    mac.treasury_6m,
    mac.treasury_1y,
    mac.treasury_2y,
    mac.treasury_5y,
    mac.treasury_7y,
    mac.treasury_10y,
    mac.treasury_30y,
    mac.mortgage_15y_fixed,
    mac.mortgage_30y_fixed,
    mac.gold_spot_usd,
    mac.silver_spot_usd,
    reg.regime,
    reg.confidence_score   AS regime_confidence,
    reg.spy_vs_200ma,
    sent.fear_greed_index,
    sent.fear_greed_label,
    sent.put_call_ratio,
    sent.insider_buy_sell_ratio,
    sent.aaii_bull_pct,
    sent.aaii_bear_pct
FROM market_daily md
LEFT JOIN macro_daily      mac  ON mac.date  = md.date
LEFT JOIN market_regimes   reg  ON reg.date  = md.date
LEFT JOIN market_sentiment sent ON sent.date = md.date;


COMMIT;
