-- ============================================================================
-- BHN Market Data Standard v2 — Step 03: fee_schedule seed
-- ============================================================================
-- Source of truth verified 2026-05-27 (Part 2 §1 of the v2 spec).
-- ON CONFLICT DO NOTHING keyed on (market, fee_name, effective_date) — idempotent.
-- When platforms change rates, INSERT a new row with a later effective_date;
-- queries filter by effective_date so historical rates remain queryable.
--
-- Apply on LA hub:
--   cat sql/market-data-standard-03-fee-schedule-seed.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- ============================================================================

BEGIN;

-- COURTYARD
INSERT INTO fee_schedule (market, fee_name, fee_type, rate, applies_to,
                          min_threshold, max_threshold, is_promotional, promo_expires_at,
                          effective_date, verified_source, notes)
VALUES
  ('courtyard','Marketplace Fee',       'platform_pct',    0.0000,'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','Courtyard official announcement; Deadspin review 2026; Sports Collectors Daily',
   '0% marketplace fee — verified across multiple sources'),
  ('courtyard','Polygon Gas (buy)',     'gas_flat',        0.01,  'buyer',  NULL, NULL, FALSE, NULL,
   '2026-05-27','Approximate Polygon L2 gas cost', NULL),
  ('courtyard','Polygon Gas (sell)',    'gas_flat',        0.01,  'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','Approximate Polygon L2 gas cost', NULL),
  ('courtyard','Redemption Handling',   'redemption_flat', 2.00,  'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','docs.courtyard.io', '$2/card during high demand periods'),
  ('courtyard','Shipping Domestic',     'shipping_flat',  12.00,  'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','FedEx estimate from Delaware vault; docs.courtyard.io', NULL),
  ('courtyard','Shipping International','shipping_flat',  35.00,  'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','Estimate — verify with Courtyard for exact rates', NULL)
ON CONFLICT ON CONSTRAINT fee_schedule_unique DO NOTHING;

-- COLLECTOR CRYPT
INSERT INTO fee_schedule (market, fee_name, fee_type, rate, applies_to,
                          min_threshold, max_threshold, is_promotional, promo_expires_at,
                          effective_date, verified_source, notes)
VALUES
  ('collector_crypt','Platform Fee',  'platform_pct', 1.0000, 'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','CoinGecko CARDS token profile', '1% platform + 1% royalty = 2% total'),
  ('collector_crypt','Royalty Fee',   'royalty_pct',  1.0000, 'seller', NULL, NULL, FALSE, NULL,
   '2026-05-27','CoinGecko CARDS token profile', NULL),
  ('collector_crypt','Solana Gas',    'gas_flat',     0.002,  'buyer',  NULL, NULL, FALSE, NULL,
   '2026-05-27','Approximate Solana transaction cost', NULL)
ON CONFLICT ON CONSTRAINT fee_schedule_unique DO NOTHING;

-- EBAY (non-store / starter store)
INSERT INTO fee_schedule (market, fee_name, fee_type, rate, applies_to,
                          min_threshold, max_threshold, is_promotional, promo_expires_at,
                          effective_date, verified_source, notes)
VALUES
  ('ebay','Final Value Fee',             'platform_pct',         13.2500,'seller', NULL,    7500.00, FALSE, NULL,
   '2026-05-27','eBay fee documentation; webgility.com analysis', '13.25% on portion up to $7,500'),
  ('ebay','FVF Above $7,500',            'platform_pct',          2.3500,'seller', 7500.01, NULL,    FALSE, NULL,
   '2026-05-27','eBay fee documentation', '2.35% on portion above $7,500'),
  ('ebay','Payment Processing',          'payment_pct',           2.9000,'seller', NULL,    NULL,    FALSE, NULL,
   '2026-05-27','eBay managed payments documentation', NULL),
  ('ebay','Per-Order Fee (over $10)',    'payment_flat',          0.40,  'seller', 10.01,   NULL,    FALSE, NULL,
   '2026-05-27','eBay fee documentation', NULL),
  ('ebay','Per-Order Fee ($10 or less)', 'payment_flat',          0.30,  'seller', NULL,    10.00,   FALSE, NULL,
   '2026-05-27','eBay fee documentation', NULL),
  ('ebay','Authenticity Guarantee',      'authentication_flat',  17.00,  'buyer',  250.00,  NULL,    FALSE, NULL,
   '2026-05-27','eBay AG docs — $12-22 range', 'Using $17 midpoint'),
  ('ebay','Shipping (graded card)',      'shipping_flat',         8.00,  'seller', NULL,    NULL,    FALSE, NULL,
   '2026-05-27','Estimated insured USPS/UPS for graded slab', NULL),
  ('ebay','FVF 50% Promo ($1K+)',        'platform_pct',         -6.6250,'seller', 1000.00, NULL,    TRUE,  '2026-05-04 00:00:00+00',
   '2026-05-27','eBay promotional page', 'Periodic — not permanent. Expired row retained for historical reference.')
ON CONFLICT ON CONSTRAINT fee_schedule_unique DO NOTHING;

-- EBAY (basic store and above)
INSERT INTO fee_schedule (market, fee_name, fee_type, rate, applies_to,
                          min_threshold, max_threshold, is_promotional, promo_expires_at,
                          effective_date, verified_source, notes)
VALUES
  ('ebay','FVF Basic Store',        'platform_pct', 12.3500,'seller', NULL,    2500.00, FALSE, NULL,
   '2026-05-27','eBay fee documentation', '12.35% on portion up to $2,500 for Basic Store and above'),
  ('ebay','FVF Basic Above $2,500', 'platform_pct',  2.3500,'seller', 2500.01, NULL,    FALSE, NULL,
   '2026-05-27','eBay fee documentation', NULL)
ON CONFLICT ON CONSTRAINT fee_schedule_unique DO NOTHING;

COMMIT;

-- Quick verification (informational, no DML):
--   SELECT market, COUNT(*), SUM(rate) FROM fee_schedule GROUP BY market;
--   Expected row counts:  courtyard=6, collector_crypt=3, ebay=10
