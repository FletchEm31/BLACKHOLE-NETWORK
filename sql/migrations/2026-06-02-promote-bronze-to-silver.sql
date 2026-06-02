-- ============================================================================
-- PokemonBHN — Silver promotion: promote_bronze_to_silver()
-- ============================================================================
-- Promotes qualifying ebay_transactions (Bronze) rows into silver_ebay_transactions.
-- Idempotent (NOT EXISTS on bronze_id) — safe to re-run; called by the
-- BRONZE_TO_SILVER_EBAY_TRANSACTIONS n8n workflow on a schedule.
--
-- Identity (card_code/set_name/card_number/edition/print_variant) is sourced from
-- master_card_catalog via the resolved card_id (the authority) — NOT the noisy Bronze
-- parse — so the Silver NOT-NULL identity columns are always populated and canonical.
-- Derived values (pbdd_grade_code, grade_numeric, grade_label) computed at promotion.
--
-- Promotion gate (ALL must pass): card_id resolved (JOIN), grader ∈ PSA/CGC/BGS/SGC/TAG,
-- grade NOT NULL, sold_price > 0, sold_date NOT NULL, item_id NOT NULL, edition NOT NULL
-- and != 'N/A' (unless the set is a promo set). sale_type mapped Bronze→Silver vocab.
--
-- "Never silently dropped": non-qualifying rows simply remain in Bronze (the permanent
-- raw layer) and promote automatically once they qualify (e.g. after card_id resolution).
-- Bronze IS the audit trail; this function is purely additive into Silver.
-- ============================================================================

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION promote_bronze_to_silver()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_promoted integer;
BEGIN
  INSERT INTO silver_ebay_transactions (
    card_code, pbdd_grade_code, card_id, set_name, card_number, edition, print_variant,
    grader, grade, grade_numeric, grade_label, cert_number, sold_price, shipping, currency,
    item_id, sold_date, sold_at, sale_type, listing_url, seller_username, seller_feedback_score,
    location, title_raw, platform, bronze_id, promotion_method
  )
  SELECT
    mcc.card_code,
    pbdd_grade_code(mcc.card_code, b.grader, b.grade),
    b.card_id,
    mcc.set_name,
    mcc.card_number,
    mcc.edition,
    COALESCE(mcc.print_variant, 'Standard'),
    b.grader,
    b.grade,
    mgc.numeric_grade,
    COALESCE(NULLIF(mgc.tier_label, ''), b.grade_label, b.grade),
    b.cert_number,
    b.sold_price,
    b.shipping,
    CASE WHEN upper(b.currency) = ANY (ARRAY['USD','GBP','EUR','CAD','AUD'])
         THEN upper(b.currency) ELSE 'USD' END,
    b.item_id,
    b.sold_date,
    b.sold_at,
    CASE b.sale_type
      WHEN 'fixed_price'    THEN 'BIN'
      WHEN 'offer_accepted' THEN 'OBO'
      WHEN 'auction'        THEN 'auction'
      ELSE NULL
    END,
    b.raw_payload ->> 'listing_url',
    b.seller_username,
    b.seller_feedback_score,
    b.location,
    b.title_raw,
    COALESCE(b.platform, 'ebay'),
    b.id,
    'exact_match'
  FROM ebay_transactions b
  JOIN master_card_catalog  mcc ON b.card_id = mcc.id
  LEFT JOIN master_set_catalog   msc ON mcc.set_name = msc.set_name
  LEFT JOIN master_grade_catalog mgc ON mgc.grader = b.grader AND mgc.raw_label = b.grade
  WHERE b.grader IN ('PSA','CGC','BGS','SGC','TAG')
    AND b.grade       IS NOT NULL
    AND b.sold_price  > 0
    AND b.sold_date   IS NOT NULL
    AND b.item_id     IS NOT NULL
    AND mcc.edition   IS NOT NULL
    AND (mcc.edition <> 'N/A' OR COALESCE(msc.is_promo, false))
    AND NOT EXISTS (SELECT 1 FROM silver_ebay_transactions s WHERE s.bronze_id = b.id);

  GET DIAGNOSTICS v_promoted = ROW_COUNT;
  RAISE NOTICE 'promote_bronze_to_silver: % rows promoted', v_promoted;
  RETURN v_promoted;
END;
$$;

GRANT EXECUTE ON FUNCTION promote_bronze_to_silver() TO n8n_user;

COMMENT ON FUNCTION promote_bronze_to_silver() IS
  'Additive, idempotent Bronze→Silver promotion. Inserts qualifying ebay_transactions rows '
  'into silver_ebay_transactions (identity from master_card_catalog via card_id; derived codes '
  'computed). Returns rows promoted. Non-qualifying rows remain in Bronze. Run by '
  'BRONZE_TO_SILVER_EBAY_TRANSACTIONS n8n workflow.';
