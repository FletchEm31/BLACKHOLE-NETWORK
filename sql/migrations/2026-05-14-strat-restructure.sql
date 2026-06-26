-- 2026-05-14-strat-restructure.sql
-- Operator restructure (2026-05-14):
--   * Active strategies are now: strat_3, strat_4, strat_6, strat_7, strat_8, strat_13
--   * Parked (status=inactive — kept on disk, not deployed):
--       strat_1_congress, strat_2_value, strat_5_pred_mkt,
--       strat_9 prediction-alpha (never registered in trading_strategies anyway),
--       strat_10/11 (planned but dropped: january-barometer, bollinger-spy)
--   * Account consolidation: strat_6/7/8 now share Account 1 (BHN-STRAT-PRIMARY,
--     <ALPACA_PAPER_ACCOUNT_ID>) — was 3 separate accounts. strat_3 stays on Account 2
--     (BHN-STRAT-FUNDAMENTAL, PA3AZX0UE3JC). strat_4 + strat_13 share Account 3
--     (BHN-STRAT-SIGNALS, PA37PRN150AG).
--   * New allocations per restructure:
--       strat_3:  $20,000  (already running)
--       strat_4:  $12,500  (already running)
--       strat_6:  $40,000  (was 5,000)
--       strat_7:  $40,000  (was 5,000) — kept disabled until 6 validates
--       strat_8:  $20,000  (was 5,000)
--       strat_13: $12,500  (new)
--   * strat_13 INSERT is idempotent (ON CONFLICT DO NOTHING). The trading-schema.sql
--     master seed has been updated for fresh deploys.
--
-- Apply on LA:
--   sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-05-14-strat-restructure.sql

\set ON_ERROR_STOP on

BEGIN;

-- 1. Park inactive strategies — status='paused' with operator-set rationale.
--    Their rows + history stay intact; they just won't run via the should_run gate.
UPDATE trading_strategies
SET status = 'paused',
    last_status_change_at = NOW(),
    last_status_change_reason = 'parked in 2026-05-14 restructure — not in current active set'
WHERE id IN ('strat_1_congress', 'strat_2_value', 'strat_5_pred_mkt')
  AND status != 'paused';

-- 2. Update capital allocations + broker notes for strat_6/7/8.
--    Notes now mention the shared BHN-STRAT-PRIMARY account.
UPDATE trading_strategies
SET capital_allocation = 40000.00,
    notes = 'Daily 09:40 ET fire; Monday signal recompute. Shared Account 1 '
            '(BHN-STRAT-PRIMARY, <ALPACA_PAPER_ACCOUNT_ID>) with strat_7/strat_8. Keys in '
            '/etc/bhn-trading/strat6.env. 5% trailing stop, 13.25% profit '
            'target, 60d max hold. Enabled via rules.json.'
WHERE id = 'strat_6_nasdaq_long';

UPDATE trading_strategies
SET capital_allocation = 40000.00,
    notes = 'Shared Account 1 (BHN-STRAT-PRIMARY, <ALPACA_PAPER_ACCOUNT_ID>) with strat_6/'
            'strat_8. Keys in /etc/bhn-trading/strat7.env. Requires margin '
            '(short QQQ + short SPY). Disabled until strat_6 validated. 5% '
            'trailing stop, 15% profit target, 60d max hold.'
WHERE id = 'strat_7_nasdaq_short';

UPDATE trading_strategies
SET capital_allocation = 20000.00,
    notes = 'Daily rebalance 15:55 ET. Shared Account 1 (BHN-STRAT-PRIMARY, '
            '<ALPACA_PAPER_ACCOUNT_ID>) with strat_6/strat_7. Keys in /etc/bhn-trading/'
            'strat8.env. 5% trailing stop, 13.25% profit target, signal-driven '
            'rotation (no max hold).'
WHERE id = 'strat_8_sector_rotation';

-- 3. Insert strat_13 if missing. Matches the seed in trading-schema.sql.
INSERT INTO trading_strategies
    (id, name, description, capital_allocation, status, live_mode_approved, cadence_seconds, notes)
VALUES
    ('strat_13_rsi_intraday', 'RSI Intraday (pipeline-testing strategy)',
     'Simple RSI-14 mean reversion on QQQ. Reads RSI from market_daily on LA. '
     'Buy QQQ when RSI<30, park JPST when RSI>70, hold otherwise. 3% trailing '
     'stop, 8% profit target, 5-day max hold. Primary purpose: generate '
     'paper_trades + signals_log volume to exercise the full pipeline.',
     12500.00, 'active', false, NULL,
     'Every 30 min during market hours (09:30-16:00 ET, Mon-Fri). BHN-STRAT-'
     'SIGNALS account (PA37PRN150AG, shared with strat_4_momentum, $12,500 '
     'each). Keys in /etc/bhn-trading/strat13.env. enabled=true on deploy.')
ON CONFLICT (id) DO UPDATE
    SET capital_allocation = EXCLUDED.capital_allocation,
        notes              = EXCLUDED.notes,
        description        = EXCLUDED.description,
        last_status_change_at = NOW();

-- 4. Sanity check — print the resulting active set.
DO $$
DECLARE
    active_ids TEXT[];
    paused_ids TEXT[];
BEGIN
    SELECT ARRAY_AGG(id ORDER BY id) INTO active_ids
    FROM trading_strategies WHERE status = 'active';
    SELECT ARRAY_AGG(id ORDER BY id) INTO paused_ids
    FROM trading_strategies WHERE status = 'paused';
    RAISE NOTICE 'After restructure:';
    RAISE NOTICE '  active = %', active_ids;
    RAISE NOTICE '  paused = %', paused_ids;
END $$;

COMMIT;

-- Post-deploy operator action items:
-- 1. Create /etc/bhn-trading/strat{6,7,8}.env on NJ with <ALPACA_PAPER_ACCOUNT_ID> credentials.
-- 2. Create /etc/bhn-trading/strat13.env on NJ with PA37PRN150AG credentials.
-- 3. Update rules.json on LA — add strat_13_rsi_intraday block, rewrite
--    strat_6/7/8 broker subblocks to point at the shared-account env vars.
--    Sync rules.json from LA → NJ via the existing rsync path.
-- 4. Install systemd units + enable timers (strat_6, strat_8, strat_13;
--    leave strat_7 disabled).
