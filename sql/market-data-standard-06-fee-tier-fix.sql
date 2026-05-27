-- ============================================================================
-- BHN Market Data Standard v2 — Step 06: fee_schedule tier column +
-- tier-aware estimate_trade_costs()
-- ============================================================================
-- Bug surfaced while verifying step 04's worked example (Part 2 §4):
--   PSA-10 $800 Courtyard → $1,100 eBay returned sell_fees=$313.90 (-$23.91
--   net, NOT profitable). Expected: ~$170 sell_fees, +$121 net, profitable.
--   Cause: estimate_trade_costs() summed BOTH eBay FVF tiers (non-store
--   13.25% AND Basic Store 12.35%) because nothing in the schema marked them
--   as mutually exclusive.
--
-- Fix:
--   1. ALTER fee_schedule ADD COLUMN tier (nullable, NULL = n/a or cross-tier).
--   2. Tag existing eBay rows: non_store / basic_store / all.
--   3. DROP + CREATE estimate_trade_costs with new p_ebay_tier parameter
--      (signature change — can't use CREATE OR REPLACE).
--
-- Apply on LA hub:
--   cat sql/market-data-standard-06-fee-tier-fix.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6A. tier column on fee_schedule
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE fee_schedule
    ADD COLUMN IF NOT EXISTS tier TEXT;

-- Allowed values (controlled vocab — additive over time as more store
-- tiers are modeled, e.g. premium_store, anchor_store, enterprise_store).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fee_schedule_tier_chk') THEN
        ALTER TABLE fee_schedule ADD CONSTRAINT fee_schedule_tier_chk
            CHECK (tier IS NULL OR tier IN (
                'all',          -- cross-tier ebay row (payment proc, AG, shipping, etc.)
                'non_store',    -- ebay non-store / starter plan
                'basic_store',  -- ebay Basic Store
                'premium_store',-- reserved
                'anchor_store'  -- reserved
            ));
    END IF;
END$$;

COMMENT ON COLUMN fee_schedule.tier IS
    'For eBay rows: which seller-plan tier this row applies to. NULL = n/a (non-eBay markets). ''all'' = applies regardless of tier. ''non_store'' / ''basic_store'' = mutually exclusive FVF tiers. estimate_trade_costs() filters by tier IS NULL OR tier = ''all'' OR tier = p_ebay_tier.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 6B. Tag existing rows
-- ─────────────────────────────────────────────────────────────────────────────
-- Mutually-exclusive FVF tiers
UPDATE fee_schedule SET tier = 'non_store'
 WHERE market = 'ebay' AND fee_name IN ('Final Value Fee','FVF Above $7,500');

UPDATE fee_schedule SET tier = 'basic_store'
 WHERE market = 'ebay' AND fee_name IN ('FVF Basic Store','FVF Basic Above $2,500');

-- Cross-tier eBay rows (payment processing, per-order, AG, shipping, promos)
UPDATE fee_schedule SET tier = 'all'
 WHERE market = 'ebay'
   AND fee_name IN (
       'Payment Processing',
       'Per-Order Fee (over $10)',
       'Per-Order Fee ($10 or less)',
       'Authenticity Guarantee',
       'Shipping (graded card)',
       'FVF 50% Promo ($1K+)'
   );

-- Non-eBay markets: tier stays NULL (n/a). Defensive update in case future
-- rows arrive without tier set — no-op against current seed.
UPDATE fee_schedule SET tier = NULL
 WHERE market IN ('courtyard','collector_crypt') AND tier IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6C. Drop + recreate estimate_trade_costs() with p_ebay_tier parameter
-- ─────────────────────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS estimate_trade_costs(TEXT, TEXT, DECIMAL, DECIMAL, TEXT);

CREATE FUNCTION estimate_trade_costs(
    p_buy_market   TEXT,
    p_sell_market  TEXT,
    p_buy_price    DECIMAL,
    p_sell_price   DECIMAL,
    p_direction    TEXT,
    p_ebay_tier    TEXT DEFAULT 'non_store'
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
    v_min_profit   DECIMAL(10,2) := 25.00;
BEGIN
    -- ── SELL-SIDE fees (platform + payment processing + royalty) ──
    SELECT COALESCE(SUM(
        CASE
            WHEN fee_type IN ('platform_pct','payment_pct','royalty_pct') THEN p_sell_price * rate / 100.0
            WHEN fee_type IN ('platform_flat','payment_flat') THEN rate
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
       AND (max_threshold IS NULL OR p_sell_price <= max_threshold)
       -- tier filter: NULL = non-eBay (n/a), 'all' = cross-tier, else must match
       AND (tier IS NULL OR tier = 'all' OR tier = p_ebay_tier);

    -- ── BUY-SIDE fees ──
    SELECT COALESCE(SUM(
        CASE
            WHEN fee_type IN ('platform_pct','payment_pct') THEN p_buy_price * rate / 100.0
            WHEN fee_type = 'gas_flat'             THEN rate
            WHEN fee_type = 'authentication_flat'  THEN rate
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
       AND (max_threshold IS NULL OR p_buy_price <= max_threshold)
       AND (tier IS NULL OR tier = 'all' OR tier = p_ebay_tier);

    -- ── Gas: also accumulate sell-side gas ──
    SELECT v_gas + COALESCE(SUM(rate), 0)
      INTO v_gas
      FROM fee_schedule
     WHERE market = p_sell_market
       AND fee_type = 'gas_flat'
       AND applies_to IN ('seller','both');

    -- ── Direction-specific physical movement costs ──
    IF p_direction IN ('courtyard_to_ebay','cc_to_ebay') THEN
        SELECT COALESCE(MAX(rate),2.00) INTO v_redemption
          FROM fee_schedule
         WHERE market = p_buy_market AND fee_type = 'redemption_flat';
        SELECT COALESCE(MAX(rate),8.00) INTO v_shipping
          FROM fee_schedule
         WHERE market = 'ebay' AND fee_type = 'shipping_flat'
           AND fee_name = 'Shipping (graded card)';
    ELSIF p_direction IN ('ebay_to_courtyard','ebay_to_cc') THEN
        SELECT COALESCE(MAX(rate),8.00) INTO v_shipping
          FROM fee_schedule
         WHERE market = 'ebay' AND fee_type = 'shipping_flat';
        SELECT COALESCE(MAX(rate),0.00) INTO v_tokenization
          FROM fee_schedule
         WHERE market = p_sell_market AND fee_type = 'tokenization_flat';
    ELSE
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

GRANT EXECUTE ON FUNCTION estimate_trade_costs(TEXT, TEXT, DECIMAL, DECIMAL, TEXT, TEXT)
    TO n8n_user, agent_reader, ehuser;

COMMENT ON FUNCTION estimate_trade_costs(TEXT, TEXT, DECIMAL, DECIMAL, TEXT, TEXT) IS
    'Pre-trade cost projection. Returns (buy_fees, sell_fees, shipping, redemption, tokenization, gas, total, net_profit, roi_pct, is_profitable). Reads fee_schedule with tier filter (defaults to non_store eBay tier). Direction governs physical movement costs.';

COMMIT;
