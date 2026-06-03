-- 2026-06-03 — Courtyard sales backfill: language + cert_number from raw_payload traits
--
-- WHY: The n8n COURTYARD-BHN | SALES-COLLECTOR INSERT SQL does not include the
-- 'language' or 'cert_number' columns, even though both are extracted from OpenSea
-- trait data in the Code node. This backfill recovers those fields from raw_payload
-- for all 546 existing rows. A follow-up n8n workflow change (needs Fletch approval)
-- should add both columns to future INSERTs.
--
-- SAFE TO RE-RUN: WHERE clauses only target NULL rows; existing non-null values untouched.
-- SNAPSHOT FIRST before running on live DB.
--
-- Preview before running (read-only):
-- SELECT id, item_id,
--   (SELECT t->>'value' FROM jsonb_array_elements(raw_payload->'nft'->'traits') t
--    WHERE t->>'trait_type' = 'Language' LIMIT 1) AS language_would_set,
--   (SELECT t->>'value' FROM jsonb_array_elements(raw_payload->'nft'->'traits') t
--    WHERE t->>'trait_type' = 'Serial' LIMIT 1) AS cert_would_set
-- FROM courtyard_sales
-- WHERE language IS NULL AND raw_payload->'nft'->'traits' IS NOT NULL
-- LIMIT 10;

BEGIN;

-- 1. Backfill language from 'Language' trait
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

-- 2. Backfill cert_number from 'Serial' trait
UPDATE courtyard_sales
SET cert_number = (
  SELECT t->>'value'
  FROM jsonb_array_elements(raw_payload->'nft'->'traits') AS t
  WHERE t->>'trait_type' = 'Serial'
  LIMIT 1
)
WHERE cert_number IS NULL
  AND raw_payload->'nft'->'traits' IS NOT NULL
  AND jsonb_array_length(raw_payload->'nft'->'traits') > 0;

-- Verify
SELECT
  COUNT(*) AS total,
  COUNT(language) AS with_language,
  COUNT(cert_number) AS with_cert_number
FROM courtyard_sales;

COMMIT;
