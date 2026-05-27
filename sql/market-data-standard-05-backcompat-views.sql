-- ============================================================================
-- BHN Market Data Standard v2 — Step 05: Back-compat views
-- ============================================================================
-- Keep existing n8n workflows + collector scripts green while they're
-- migrated one at a time. INSERT/UPDATE through these views auto-routes to
-- the renamed real tables (simple views are auto-updatable in PG).
--
-- DO NOT DROP these views in this migration. Operator drops each one manually
-- after `grep -r "<old_name>" n8n-workflows/ scripts/` returns empty AND each
-- workflow has been re-tested. Precedent: `card_catalog` view (kept after
-- master_card_catalog rename) per project_card_catalog_queue.
--
-- Apply on LA hub:
--   cat sql/market-data-standard-05-backcompat-views.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- ============================================================================

BEGIN;

CREATE OR REPLACE VIEW ebay_listings         AS SELECT * FROM ebay_asks;
CREATE OR REPLACE VIEW sold_listings         AS SELECT * FROM ebay_transactions;
CREATE OR REPLACE VIEW courtyard_listings    AS SELECT * FROM courtyard_asks;
CREATE OR REPLACE VIEW courtyard_sales       AS SELECT * FROM courtyard_transactions;
CREATE OR REPLACE VIEW collector_crypt_sales AS SELECT * FROM collector_crypt_transactions;

COMMENT ON VIEW ebay_listings IS
    'Back-compat view → ebay_asks. Drop after all n8n workflows + collector scripts migrated. v2 standard 2026-05-27.';
COMMENT ON VIEW sold_listings IS
    'Back-compat view → ebay_transactions. Drop after all consumers migrated.';
COMMENT ON VIEW courtyard_listings IS
    'Back-compat view → courtyard_asks. Drop after all consumers migrated.';
COMMENT ON VIEW courtyard_sales IS
    'Back-compat view → courtyard_transactions. Drop after all consumers migrated.';
COMMENT ON VIEW collector_crypt_sales IS
    'Back-compat view → collector_crypt_transactions. Drop after all consumers migrated.';

-- Mirror underlying-table grants onto the views so role-based access is intact.
GRANT SELECT ON ebay_listings, sold_listings, courtyard_listings,
                courtyard_sales, collector_crypt_sales
    TO agent_reader, grafana_reader, ehuser;
GRANT INSERT, UPDATE ON ebay_listings, courtyard_listings TO log_shipper;
GRANT INSERT          ON sold_listings, courtyard_sales, collector_crypt_sales TO log_shipper;

COMMIT;

-- After applying, run these to map remaining consumers (informational):
--   grep -r "ebay_listings\|sold_listings\|courtyard_listings\|courtyard_sales\|collector_crypt_sales" \
--        n8n-workflows/ scripts/
-- Each match is a workflow/script still pinned to the old name. Drop each
-- view only after the corresponding consumers have been re-pointed.
