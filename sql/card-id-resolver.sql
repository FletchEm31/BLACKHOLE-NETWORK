-- card-id-resolver.sql
-- BHN — card_id surrogate-key plumbing for cross-platform observation tables.
-- Built 2026-05-27 to unblock the Courtyard arbitrage signal generator.
--
-- Identity model (data-standard §2):
--   card_id     = master_card_catalog.id            — WHICH KIND of card
--   cert_number = one physical graded slab           — ONE SPECIFIC COPY
--   card_number = within-set number (a field, not a key — repeats per set)
--
-- Without card_id, the eBay ↔ Courtyard arbitrage join is fuzzy text matching
-- on (card_name, grader, grade). With card_id, it's exact-key equality.
--
-- Apply on LA hub (stdin pipe — postgres can't cd into /root):
--   cat sql/card-id-resolver.sql | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP A — card_id columns on observation tables (NULLABLE — never block ingest)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE ebay_listings           ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES master_card_catalog(id);
ALTER TABLE sold_listings           ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES master_card_catalog(id);
ALTER TABLE courtyard_listings      ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES master_card_catalog(id);
ALTER TABLE courtyard_sales         ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES master_card_catalog(id);
ALTER TABLE collector_crypt_sales   ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES master_card_catalog(id);
ALTER TABLE tokenized_arbitrage_signals
                                    ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES master_card_catalog(id);

CREATE INDEX IF NOT EXISTS ebay_listings_card_id_idx          ON ebay_listings(card_id);
CREATE INDEX IF NOT EXISTS sold_listings_card_id_idx          ON sold_listings(card_id);
CREATE INDEX IF NOT EXISTS courtyard_listings_card_id_idx     ON courtyard_listings(card_id);
CREATE INDEX IF NOT EXISTS courtyard_sales_card_id_idx        ON courtyard_sales(card_id);
CREATE INDEX IF NOT EXISTS collector_crypt_sales_card_id_idx  ON collector_crypt_sales(card_id);
CREATE INDEX IF NOT EXISTS tokenized_arbitrage_signals_card_id_idx
                                                              ON tokenized_arbitrage_signals(card_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP B — Migrate master_card_catalog.card_number from '#NN' → bare 'NN'
-- (data-standard §9 pending item — scoped to this one column for now)
-- Only strips a leading '#'; leaves rows that are already bare untouched and
-- preserves any non-numeric suffix (e.g. '#4a' → '4a', '#SP4' → 'SP4').
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE master_card_catalog
   SET card_number = regexp_replace(card_number, '^#', '', 'g')
 WHERE card_number LIKE '#%';


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP C — resolve_card_id() — three-attempt cascade, NULL on miss.
-- Both sides of the card_number comparison are normalized (strip leading '#'
-- and trailing '/denominator') so the function survives any future drift on
-- either the catalog or the ingest side.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION resolve_card_id(
    p_card_name     TEXT,
    p_set_name      TEXT,
    p_card_number   TEXT,
    p_edition       TEXT DEFAULT NULL,
    p_print_variant TEXT DEFAULT NULL
) RETURNS INTEGER AS $$
DECLARE
    v_id INTEGER;
    v_card_number TEXT;
BEGIN
    -- Normalize incoming card_number: trim, strip leading '#', drop '/N' denominator.
    v_card_number := regexp_replace(
                       btrim(COALESCE(p_card_number, '')),
                       '^#|/\d+$', '', 'g'
                     );

    -- Normalize edition to canonical vocab.
    p_edition := CASE
        WHEN p_edition ILIKE '1st%'    THEN '1st Edition'
        WHEN p_edition ILIKE 'shadow%' THEN 'Shadowless'
        WHEN p_edition ILIKE 'unlim%'  THEN 'Unlimited'
        ELSE COALESCE(p_edition, 'Unlimited')
    END;

    -- Attempt 1: set + card_number + edition (most reliable).
    -- Defensive normalize on the master_card_catalog side too.
    IF v_card_number <> '' THEN
        SELECT id INTO v_id FROM master_card_catalog
         WHERE set_name = p_set_name
           AND regexp_replace(card_number, '^#', '', 'g') = v_card_number
           AND edition = p_edition
         LIMIT 1;
        IF v_id IS NOT NULL THEN RETURN v_id; END IF;
    END IF;

    -- Attempt 2: set + card_number (edition unknown — take first match).
    IF v_card_number <> '' THEN
        SELECT id INTO v_id FROM master_card_catalog
         WHERE set_name = p_set_name
           AND regexp_replace(card_number, '^#', '', 'g') = v_card_number
         ORDER BY edition
         LIMIT 1;
        IF v_id IS NOT NULL THEN RETURN v_id; END IF;
    END IF;

    -- Attempt 3: card_name + set_name + edition (no card_number).
    IF p_card_name IS NOT NULL AND p_set_name IS NOT NULL THEN
        SELECT id INTO v_id FROM master_card_catalog
         WHERE set_name = p_set_name
           AND lower(card_name) = lower(p_card_name)
           AND edition = p_edition
         LIMIT 1;
        IF v_id IS NOT NULL THEN RETURN v_id; END IF;
    END IF;

    -- No match — caller treats NULL as "still insert, exclude from arbitrage".
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

GRANT EXECUTE ON FUNCTION resolve_card_id(TEXT, TEXT, TEXT, TEXT, TEXT)
    TO n8n_user, log_shipper, ehuser;


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP D — Backfill card_id on existing rows where we have enough fields.
-- Card_number isn't always available on sold_listings/ebay_listings yet (per
-- data-standard §9 — those columns aren't added to those tables), so the
-- resolver lands on Attempt 3 (card_name + set_name + edition) for most rows.
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE sold_listings
   SET card_id = resolve_card_id(card_name, set_name, NULL, NULL, NULL)
 WHERE card_id IS NULL
   AND set_name IS NOT NULL;

UPDATE ebay_listings
   SET card_id = resolve_card_id(card_name, set_name, NULL, NULL, NULL)
 WHERE card_id IS NULL
   AND set_name IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP E — Resolution rate report. Brief target: ≥80% before Task 4 ships.
-- ─────────────────────────────────────────────────────────────────────────────
SELECT 'sold_listings' AS tbl,
       COUNT(*)                                                  AS total_with_set,
       COUNT(card_id)                                            AS resolved,
       ROUND(COUNT(card_id)::numeric / NULLIF(COUNT(*),0) * 100, 1) AS pct_resolved
  FROM sold_listings
 WHERE set_name IS NOT NULL
UNION ALL
SELECT 'ebay_listings',
       COUNT(*),
       COUNT(card_id),
       ROUND(COUNT(card_id)::numeric / NULLIF(COUNT(*),0) * 100, 1)
  FROM ebay_listings
 WHERE set_name IS NOT NULL;

-- If pct_resolved < 80, inspect the failing (set_name, card_name) pairs:
--   SELECT set_name, card_name, COUNT(*) AS unresolved
--     FROM sold_listings
--    WHERE card_id IS NULL AND set_name IS NOT NULL
--    GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
-- Fix the resolver (not the data) if a pattern emerges, then re-run STEP D.
