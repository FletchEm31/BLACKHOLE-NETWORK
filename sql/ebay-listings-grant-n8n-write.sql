-- ============================================================================
-- PokemonBHN — restore n8n write access to ebay_listings after v2 rename
-- ============================================================================
-- Applied on LA hub 2026-05-29 (eventhorizon).
--
-- BACKGROUND
--   market-data-standard-01-renames.sql renamed table ebay_listings -> ebay_asks
--   and -05-backcompat-views.sql recreated ebay_listings as a view over ebay_asks.
--   That view granted INSERT/UPDATE only to log_shipper. The 8 vintage Pokémon
--   n8n workflows connect as role `ehuser` (credential "Postgres EventHorizon"),
--   which had only SELECT on the view — so every Insert/Upsert node failed with
--   "permission denied for view ebay_listings".
--
-- VIEW SEMANTICS
--   ebay_listings is a plain view owned by `postgres` (NOT security_invoker), so
--   base-table + sequence access during a write runs as the view owner. The
--   invoking role therefore needs privileges only on the VIEW for the current
--   (write-through-view) path. The base-table grants below are forward-looking
--   for when the workflows are eventually repointed to ebay_asks directly
--   (n8n_user already owns ebay_asks, hence its implicit full access).
--
-- Idempotent: GRANT is safe to re-run.
-- ============================================================================

BEGIN;

-- Immediate fix: ehuser writes through the ebay_listings view.
GRANT INSERT, UPDATE ON ebay_listings TO ehuser;

-- Robustness: also cover n8n_user on the view (it already owns the base table).
GRANT SELECT, INSERT, UPDATE ON ebay_listings TO n8n_user;

-- Forward-looking base-table coverage for a future repoint to ebay_asks.
GRANT INSERT, UPDATE ON ebay_asks TO ehuser;
GRANT USAGE, SELECT ON SEQUENCE ebay_asks_id_seq TO ehuser;

COMMIT;
