-- 2026-06-03 — Courtyard sales backfill: language from raw_payload traits
--
-- WHY: The n8n COURTYARD-BHN | SALES-COLLECTOR INSERT SQL does not include the
-- 'language' column, even though it is extracted from OpenSea trait data in the
-- Code node. This backfill recovers language from raw_payload for all existing rows.
-- Note: courtyard_sales has no cert_number column (the Serial trait has no column
-- to map to). A follow-up n8n workflow change (needs Fletch approval) should add
-- language to future INSERTs.
--
-- APPLIED: 2026-06-03 — 546/546 rows updated (English 391, Japanese 152,
--          Chinese 2, Korean 1). SAFE TO RE-RUN (targets only NULL rows).

BEGIN;

UPDATE courtyard_sales
SET language = (
  SELECT t->>'value'
  FROM jsonb_array_elements(raw_payload->'nft'->'traits') AS t
  WHERE t->>'trait_type' = 'Language'
  LIMIT 1
)
WHERE language IS NULL
  AND raw_payload->'nft'->'traits' IS NOT NULL
  AND jsonb_array_length(raw_payload->'nft'->'traits') > 0;

SELECT COUNT(*) AS total, COUNT(language) AS with_language FROM courtyard_sales;

COMMIT;
