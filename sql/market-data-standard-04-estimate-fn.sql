-- ============================================================================
-- BHN Market Data Standard v2 — Step 04: estimate_trade_costs() + signal extension
-- ============================================================================
-- Part 2 §2 of the v2 spec. Pre-trade cost calculator used by the arbitrage
-- signal generator BEFORE a signal fires. Reads fee_schedule (Step 02+03).
--
-- Plus Part 2 §4: extends tokenized_arbitrage_signals with the 10 estimate
-- columns so signals carry their own profitability projection.
--
-- Apply on LA hub:
--   cat sql/market-data-standard-04-estimate-fn.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4A. estimate_trade_costs() — pre-trade cost projector
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION estimate_trade_costs(
    p_buy_market   TEXT,
    p_sell_market  TEXT,
    p_buy_price    DECIMAL,
    p_sell_price   DECIMAL,
    p_direction    TEXT
) RETURNS TABLE (
    buy_fees_est       DECIMAL(10,2),
    sell_fees_est      DECIMAL(10,2),
    shipping_est       DECIMAL(10,2),
    redemption_est     DECIMAL(10,2),
    tokenization_est   DECIMAL(10,2),
    gas_est            DECIMAL(10,2),
    total_costs_est    DECIMAL(10,2),
    net_profit_est     DECIMAL(10,2),
    roi_est_pct        DECIMAL(6,2),
    is_profitable      BOOLEAN
) AS $$
DECLARE
    v_buy_fees     DECIMAL(10,2) := 0;
    v_sell_fees    DECIMAL(10,2) := 0;
    v_shipping     DECIMAL(10,2) := 0;
    v_redemption   DECIMAL(10,2) := 0;
    v_tokenization DECIMAL(10,2) := 0;
    v_gas          DECIMAL(10,2) := 0;
    v_total        DECIMAL(10,2);
    v_net          DECIMAL(10,2);
    v_min_profit   DECIMAL(10,2) := 25.00;   -- default minimum profit threshold
BEGIN
    -- ── SELL-SIDE fees (platform + payment processing) ──
    -- platform %: applies against the sell price
    SELECT COALESCE(SUM(
        CASE
            WHEN fee_type = 'platform_pct' THEN p_sell_price * rate / 100.0
            WHEN fee_type = 'payment_pct'  THEN p_sell_price * rate / 100.0
            WHEN fee_type = 'royalty_pct'  THEN p_sell_price * rate / 100.0
            WHEN fee_type = 'platform_flat' OR fee_type = 'payment_flat' THEN rate
            ELSE 0
        END
    ), 0)
      INTO v_sell_fees
      FROM fee_schedule
     WHERE market = p_sell_market
       AND applies_to IN ('seller','both')
       AND fee_type IN ('platform_pct','platform_flat','payment_pct','payment_flat','royalty_pct')
       AND (is_promotional = FALSE OR (promo_expires_at IS NOT NULL AND promo_expires_at > NOW()))
       AND (min_threshold IS NULL OR p_sell_price >= min_threshold)
       AND (max_threshold IS NULL OR p_sell_price <= max_threshold);

    -- ── BUY-SIDE fees (typically just buyer-side gas / auth) ──
    SELECT COALESCE(SUM(
        CASE
            WHEN fee_type = 'platform_pct' OR fee_type = 'payment_pct' THEN p_buy_price * rate / 100.0
            WHEN fee_type = 'gas_flat'                                 THEN rate
            WHEN fee_type = 'authentication_flat'                      THEN rate
            ELSE 0
        END
    ), 0)
      INTO v_buy_fees
      FROM fee_schedule
     WHERE market = p_buy_market
       AND applies_to IN ('buyer','both')
       AND fee_type IN ('platform_pct','payment_pct','gas_flat','authentication_flat')
       AND (is_promotional = FALSE OR (promo_expires_at IS NOT NULL AND promo_expires_at > NOW()))
       AND (min_threshold IS NULL OR p_buy_price >= min_threshold)
       AND (max_threshold IS NULL OR p_buy_price <= max_threshold);

    -- ── Gas: also accumulate sell-side gas (Polygon/Solana) ──
    SELECT v_gas + COALESCE(SUM(rate), 0)
      INTO v_gas
      FROM fee_schedule
     WHERE market = p_sell_market
       AND fee_type = 'gas_flat'
       AND applies_to IN ('seller','both');

    -- ── Direction-specific physical movement costs ──
    IF p_direction IN ('courtyard_to_ebay','cc_to_ebay') THEN
        -- redeem token → ship physical → list on eBay
        SELECT COALESCE(MAX(rate),2.00) INTO v_redemption
          FROM fee_schedule
         WHERE market = p_buy_market AND fee_type = 'redemption_flat';
        SELECT COALESCE(MAX(rate),8.00) INTO v_shipping
          FROM fee_schedule
         WHERE market = 'ebay' AND fee_type = 'shipping_flat'
           AND fee_name = 'Shipping (graded card)';
    ELSIF p_direction IN ('ebay_to_courtyard','ebay_to_cc') THEN
        -- ship physical to vault → tokenize → list on tokenized market
        SELECT COALESCE(MAX(rate),8.00) INTO v_shipping
          FROM fee_schedule
         WHERE market = 'ebay' AND fee_type = 'shipping_flat';
        SELECT COALESCE(MAX(rate),0.00) INTO v_tokenization
          FROM fee_schedule
         WHERE market = p_sell_market AND fee_type = 'tokenization_flat';
    ELSE
        -- within-platform or token-to-token: no physical movement
        v_shipping   := 0;
        v_redemption := 0;
    END IF;

    v_total := COALESCE(v_buy_fees,0) + COALESCE(v_sell_fees,0)
             + COALESCE(v_shipping,0) + COALESCE(v_redemption,0)
             + COALESCE(v_tokenization,0) + COALESCE(v_gas,0);

    v_net := p_sell_price - p_buy_price - v_total;

    RETURN QUERY SELECT
        v_buy_fees,
        v_sell_fees,
        v_shipping,
        v_redemption,
        v_tokenization,
        v_gas,
        v_total,
        v_net,
        CASE WHEN p_buy_price > 0 THEN ROUND((v_net / p_buy_price * 100)::numeric, 2)::DECIMAL(6,2) ELSE NULL END,
        (v_net > v_min_profit);
END;
$$ LANGUAGE plpgsql STABLE;

GRANT EXECUTE ON FUNCTION estimate_trade_costs(TEXT, TEXT, DECIMAL, DECIMAL, TEXT)
    TO n8n_user, agent_reader, ehuser;

COMMENT ON FUNCTION estimate_trade_costs(TEXT, TEXT, DECIMAL, DECIMAL, TEXT) IS
    'Pre-trade cost projection. Returns (buy_fees, sell_fees, shipping, redemption, tokenization, gas, total, net_profit, roi_pct, is_profitable). Reads fee_schedule. Direction governs whether physical movement costs apply.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 4B. tokenized_arbitrage_signals — add the 10 estimate columns (Part 2 §4)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_buy_fees        DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_sell_fees       DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_shipping        DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_redemption      DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_tokenization    DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_gas             DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_total_costs     DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_net_profit      DECIMAL(10,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS est_roi_pct         DECIMAL(6,2);
ALTER TABLE tokenized_arbitrage_signals ADD COLUMN IF NOT EXISTS is_profitable_est   BOOLEAN;

COMMIT;
