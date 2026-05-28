-- Populate the 6 market_*_est columns on [market]_transactions tables.
-- Per BHN-MARKET-DATA-STANDARD-PART2-FEES-AND-PRICING.txt §5:
--   For observed third-party sales, estimate what the seller netted by
--   summing seller-side fees from fee_schedule at the published rates.
--
-- Touches three tables (same shape, same trigger pattern):
--   ebay_transactions, courtyard_transactions, collector_crypt_transactions
-- Idempotent: CREATE OR REPLACE + DROP TRIGGER IF EXISTS guards.

BEGIN;

-- ── helper: compute the 6 estimate values for one (market, sold_price) pair ─
-- For eBay the default tier is 'non_store' (most-conservative FVF rate); when
-- the operator becomes a Basic Store, pass 'basic_store' instead. Tokenized
-- markets ignore tier.
CREATE OR REPLACE FUNCTION compute_market_seller_estimates(
  p_market      TEXT,
  p_sold_price  NUMERIC,
  p_ebay_tier   TEXT DEFAULT 'non_store'
) RETURNS TABLE (
  platform_fee_est    NUMERIC,
  processing_fee_est  NUMERIC,
  shipping_est        NUMERIC,
  auth_fee_est        NUMERIC,
  total_costs_est     NUMERIC,
  net_to_seller_est   NUMERIC
) AS $$
DECLARE
  v_platform   NUMERIC := 0;
  v_processing NUMERIC := 0;
  v_shipping   NUMERIC := 0;
  v_auth       NUMERIC := 0;
  v_gas        NUMERIC := 0;
BEGIN
  IF p_sold_price IS NULL OR p_sold_price <= 0 THEN
    platform_fee_est   := NULL;
    processing_fee_est := NULL;
    shipping_est       := NULL;
    auth_fee_est       := NULL;
    total_costs_est    := NULL;
    net_to_seller_est  := NULL;
    RETURN NEXT;
    RETURN;
  END IF;

  -- Platform: platform_pct + platform_flat + royalty_pct (Collector Crypt has both)
  SELECT COALESCE(SUM(
    CASE
      WHEN fee_type IN ('platform_pct','royalty_pct') THEN p_sold_price * rate / 100
      WHEN fee_type = 'platform_flat'                 THEN rate
      ELSE 0
    END
  ), 0)
  INTO v_platform
  FROM fee_schedule
  WHERE market = p_market
    AND fee_type IN ('platform_pct','platform_flat','royalty_pct')
    AND applies_to IN ('seller','both')
    AND (is_promotional = FALSE OR promo_expires_at > NOW())
    AND (min_threshold IS NULL OR p_sold_price >= min_threshold)
    AND (max_threshold IS NULL OR p_sold_price <= max_threshold)
    AND (tier IS NULL OR tier = 'all' OR tier = p_ebay_tier);

  -- Payment processing: payment_pct + payment_flat (eBay 2.9% + per-order)
  SELECT COALESCE(SUM(
    CASE
      WHEN fee_type = 'payment_pct'  THEN p_sold_price * rate / 100
      WHEN fee_type = 'payment_flat' THEN rate
      ELSE 0
    END
  ), 0)
  INTO v_processing
  FROM fee_schedule
  WHERE market = p_market
    AND fee_type IN ('payment_pct','payment_flat')
    AND applies_to IN ('seller','both')
    AND (is_promotional = FALSE OR promo_expires_at > NOW())
    AND (min_threshold IS NULL OR p_sold_price >= min_threshold)
    AND (max_threshold IS NULL OR p_sold_price <= max_threshold)
    AND (tier IS NULL OR tier = 'all' OR tier = p_ebay_tier);

  -- Shipping: domestic only (skip international; assume buyer is US for comps).
  -- For tokenized sales (Courtyard/CC), the SALE itself has no shipping —
  -- shipping_flat rows for those markets describe the *redemption* shipping,
  -- not the on-chain sale. Filter to eBay + skip international/redemption rows.
  SELECT COALESCE(SUM(
    CASE
      WHEN fee_type = 'shipping_flat' THEN rate
      WHEN fee_type = 'shipping_pct'  THEN p_sold_price * rate / 100
      ELSE 0
    END
  ), 0)
  INTO v_shipping
  FROM fee_schedule
  WHERE market = p_market
    AND fee_type IN ('shipping_flat','shipping_pct')
    AND applies_to IN ('seller','both')
    AND fee_name NOT ILIKE '%international%'
    AND fee_name NOT ILIKE '%redemption%'
    AND (is_promotional = FALSE OR promo_expires_at > NOW())
    -- For Courtyard sales the seller doesn't pay shipping at sale time
    -- (that's a redemption-only cost). Restrict shipping_flat to eBay.
    AND (p_market = 'ebay' OR fee_type = 'shipping_pct');

  -- Authentication (eBay AG): only applies above $250 threshold
  SELECT COALESCE(SUM(rate), 0)
  INTO v_auth
  FROM fee_schedule
  WHERE market = p_market
    AND fee_type = 'authentication_flat'
    AND applies_to IN ('buyer','seller','both')  -- AG is buyer-paid but reduces net-to-seller proxy; include
    AND (is_promotional = FALSE OR promo_expires_at > NOW())
    AND (min_threshold IS NULL OR p_sold_price >= min_threshold)
    AND (max_threshold IS NULL OR p_sold_price <= max_threshold);

  -- Gas (Courtyard/Collector Crypt): seller-side native chain cost
  SELECT COALESCE(SUM(rate), 0)
  INTO v_gas
  FROM fee_schedule
  WHERE market = p_market
    AND fee_type = 'gas_flat'
    AND applies_to IN ('seller','both')
    AND (is_promotional = FALSE OR promo_expires_at > NOW());

  platform_fee_est   := v_platform;
  processing_fee_est := v_processing;
  shipping_est       := v_shipping;
  auth_fee_est       := v_auth;
  total_costs_est    := v_platform + v_processing + v_shipping + v_auth + v_gas;
  net_to_seller_est  := p_sold_price - total_costs_est;
  RETURN NEXT;
END;
$$ LANGUAGE plpgsql STABLE;

GRANT EXECUTE ON FUNCTION compute_market_seller_estimates(TEXT, NUMERIC, TEXT)
  TO n8n_user, agent_reader, ehuser, log_shipper;

-- ── trigger functions: one per table (each hardcodes its market name) ───────
CREATE OR REPLACE FUNCTION ebay_transactions_fill_market_est()
RETURNS TRIGGER AS $$
DECLARE r RECORD;
BEGIN
  IF NEW.sold_price IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO r FROM compute_market_seller_estimates('ebay', NEW.sold_price, 'non_store');
  NEW.market_platform_fee_est   := r.platform_fee_est;
  NEW.market_processing_fee_est := r.processing_fee_est;
  NEW.market_shipping_est       := r.shipping_est;
  NEW.market_auth_fee_est       := r.auth_fee_est;
  NEW.market_total_costs_est    := r.total_costs_est;
  NEW.market_net_to_seller_est  := r.net_to_seller_est;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION courtyard_transactions_fill_market_est()
RETURNS TRIGGER AS $$
DECLARE r RECORD;
BEGIN
  IF NEW.sold_price IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO r FROM compute_market_seller_estimates('courtyard', NEW.sold_price);
  NEW.market_platform_fee_est   := r.platform_fee_est;
  NEW.market_processing_fee_est := r.processing_fee_est;
  NEW.market_shipping_est       := r.shipping_est;
  NEW.market_auth_fee_est       := r.auth_fee_est;
  NEW.market_total_costs_est    := r.total_costs_est;
  NEW.market_net_to_seller_est  := r.net_to_seller_est;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION collector_crypt_transactions_fill_market_est()
RETURNS TRIGGER AS $$
DECLARE r RECORD;
BEGIN
  IF NEW.sold_price IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO r FROM compute_market_seller_estimates('collector_crypt', NEW.sold_price);
  NEW.market_platform_fee_est   := r.platform_fee_est;
  NEW.market_processing_fee_est := r.processing_fee_est;
  NEW.market_shipping_est       := r.shipping_est;
  NEW.market_auth_fee_est       := r.auth_fee_est;
  NEW.market_total_costs_est    := r.total_costs_est;
  NEW.market_net_to_seller_est  := r.net_to_seller_est;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── register triggers (BEFORE so we mutate NEW in-place; covers UPDATE of sold_price too)
DROP TRIGGER IF EXISTS ebay_transactions_market_est_trg            ON ebay_transactions;
CREATE TRIGGER         ebay_transactions_market_est_trg
  BEFORE INSERT OR UPDATE OF sold_price ON ebay_transactions
  FOR EACH ROW EXECUTE FUNCTION ebay_transactions_fill_market_est();

DROP TRIGGER IF EXISTS courtyard_transactions_market_est_trg       ON courtyard_transactions;
CREATE TRIGGER         courtyard_transactions_market_est_trg
  BEFORE INSERT OR UPDATE OF sold_price ON courtyard_transactions
  FOR EACH ROW EXECUTE FUNCTION courtyard_transactions_fill_market_est();

DROP TRIGGER IF EXISTS collector_crypt_transactions_market_est_trg ON collector_crypt_transactions;
CREATE TRIGGER         collector_crypt_transactions_market_est_trg
  BEFORE INSERT OR UPDATE OF sold_price ON collector_crypt_transactions
  FOR EACH ROW EXECUTE FUNCTION collector_crypt_transactions_fill_market_est();

-- ── backfill existing rows (no-op for rows where sold_price IS NULL) ────────
UPDATE ebay_transactions            SET sold_price = sold_price WHERE sold_price IS NOT NULL;
UPDATE courtyard_transactions       SET sold_price = sold_price WHERE sold_price IS NOT NULL;
UPDATE collector_crypt_transactions SET sold_price = sold_price WHERE sold_price IS NOT NULL;

COMMIT;
