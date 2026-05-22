-- pokemonbhn-master-set-catalog-schema.sql
-- PokemonBHN set dimension. One row per WOTC set: canonical name, release year / era, card count,
-- the STANDARD editions a normal card in the set comes in, a promo flag, and the PSA pop heading
-- mapping (absorbs infrastructure/scrapers/psa-sets.json). master_card_catalog.set_name is
-- FK-bound to this table, so a card can only belong to a known set.
--
-- legal_editions = the standard editions for a NORMAL card; stamp/promo prints (Prerelease,
-- W Stamp, Gold Border, promo types) carry edition='N/A' and are intentionally NOT constrained by it.
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/pokemonbhn-master-set-catalog-schema.sql

CREATE TABLE IF NOT EXISTS master_set_catalog (
    set_name        TEXT PRIMARY KEY,                 -- canonical set name (FK target for master_card_catalog)
    release_year    INT,                              -- English release year
    era             TEXT NOT NULL DEFAULT 'WOTC',
    total_cards     INT,                              -- distinct cards in the set
    legal_editions  TEXT[] NOT NULL,                  -- standard editions a normal card comes in
    is_promo        BOOLEAN NOT NULL DEFAULT FALSE,   -- promo set (editions = {N/A})
    psa_category_id INT NOT NULL DEFAULT 156940,      -- PSA "TCG Cards" category (constant)
    psa_headings    JSONB,                            -- [{year,slug,heading_id,note?}] for POST /Pop/GetSetItems
    verify          BOOLEAN NOT NULL DEFAULT FALSE,   -- mapping still needs a human eyeball
    notes           TEXT
);

INSERT INTO master_set_catalog
    (set_name, release_year, total_cards, legal_editions, is_promo, psa_headings, verify, notes)
VALUES
 ('Base Set', 1999, 102, ARRAY['1st Edition','Shadowless','Unlimited'], FALSE,
   '[{"year":"1999","slug":"pokemon-game","heading_id":57801}]'::jsonb, FALSE, NULL),
 ('Fossil', 1999, 62, ARRAY['1st Edition','Unlimited'], FALSE,
   '[{"year":"1999","slug":"pokemon-fossil","heading_id":57617}]'::jsonb, FALSE, NULL),
 ('Jungle', 1999, 64, ARRAY['1st Edition','Unlimited'], FALSE,
   '[{"year":"1999","slug":"pokemon-jungle","heading_id":58977}]'::jsonb, FALSE, NULL),
 ('Team Rocket', 2000, 83, ARRAY['1st Edition','Unlimited'], FALSE,
   '[{"year":"2000","slug":"pokemon-rocket","heading_id":61534}]'::jsonb, FALSE, NULL),
 ('Gym Heroes', 2000, 132, ARRAY['1st Edition','Unlimited'], FALSE,
   '[{"year":"2000","slug":"pokemon-gym-heroes","heading_id":58312}]'::jsonb, FALSE, NULL),
 ('Gym Challenge', 2000, 132, ARRAY['1st Edition','Unlimited'], FALSE,
   '[{"year":"2000","slug":"pokemon-gym-challenge","heading_id":58311}]'::jsonb, FALSE, NULL),
 ('Best of Game', 2002, 9, ARRAY['N/A'], TRUE,
   '[{"year":"2003","slug":"pokemon-best-game-promo","heading_id":100603}]'::jsonb, FALSE,
   'Promo set; print_variants include Winner/Jumbo.'),
 ('Wizards Black Star Promos', 1999, 53, ARRAY['N/A'], TRUE,
   '[{"year":"2000","slug":"pokemon-promo-black-star","heading_id":81092,"note":"foreign-heavy, ~#1-29"},
     {"year":"2001","slug":"pokemon-promo-black-star","heading_id":81226},
     {"year":"2003","slug":"pokemon-black-star-promo","heading_id":57939},
     {"year":"2006","slug":"pokemon-black-star-promo","heading_id":101616}]'::jsonb, TRUE,
   'PSA fragments across 4 year-headings; scraper navigates headings[0] to clear Cloudflare then fetches all heading_ids. 2001/2006 slugs best-guess (nav uses headings[0] only).')
ON CONFLICT (set_name) DO NOTHING;

-- Bind card identity to a known set (idempotent).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'master_card_catalog_set_fk') THEN
    ALTER TABLE master_card_catalog
      ADD CONSTRAINT master_card_catalog_set_fk
      FOREIGN KEY (set_name) REFERENCES master_set_catalog (set_name);
  END IF;
END $$;

GRANT SELECT ON master_set_catalog TO ehuser;       -- scrapers read set -> PSA heading mapping
GRANT SELECT ON master_set_catalog TO agent_reader; -- HORIZON reads

COMMENT ON TABLE master_set_catalog IS
    'PokemonBHN set dimension: one row per WOTC set (year/era, card count, standard editions, promo flag, PSA heading mapping). Authority for valid set_name (FK target for master_card_catalog); absorbs psa-sets.json.';
