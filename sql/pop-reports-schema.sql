-- pop-reports-schema.sql
-- Graded-card population reports. One row per (grader, set, card, grade) — populated by
-- infrastructure/scrapers/cgc-pop-*.js. Currently sourced from CGC; schema is grader-agnostic
-- so PSA / Beckett can land in the same table later.
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/pop-reports-schema.sql

CREATE TABLE IF NOT EXISTS pop_reports (
    id            BIGSERIAL PRIMARY KEY,
    grader        TEXT NOT NULL,                      -- "CGC", "PSA", "BGS", ...
    card_set      TEXT NOT NULL,                      -- "Team Rocket 1st Edition", ...
    card_name     TEXT NOT NULL,                      -- "Dark Charizard (2000) Holo Rare"
    card_number   TEXT NOT NULL DEFAULT '',           -- "4/82" (kept as-is from source)
    grade         TEXT NOT NULL,                      -- "10", "9.5", "Gem Mint 10", "AU", ...
    population    INTEGER NOT NULL,                   -- count at scrape time
    source_url    TEXT,                               -- exact page the row was pulled from
    scraped_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pop_reports_unique
        UNIQUE (grader, card_set, card_name, card_number, grade)
);

CREATE INDEX IF NOT EXISTS pop_reports_set_idx
    ON pop_reports (card_set);
CREATE INDEX IF NOT EXISTS pop_reports_grader_set_grade_idx
    ON pop_reports (grader, card_set, grade);
CREATE INDEX IF NOT EXISTS pop_reports_scraped_idx
    ON pop_reports (scraped_at DESC);

-- The scraper inserter writes as ehuser
GRANT SELECT, INSERT, UPDATE ON pop_reports TO ehuser;
GRANT USAGE, SELECT ON SEQUENCE pop_reports_id_seq TO ehuser;

-- HORIZON reads
GRANT SELECT ON pop_reports TO agent_reader;

COMMENT ON TABLE pop_reports IS
    'Graded-card population counts per (grader, set, card, grade). Populated by CGC scrapers under infrastructure/scrapers/.';
COMMENT ON COLUMN pop_reports.grade IS
    'Verbatim grade string from the source (e.g. "Gem Mint 10", "9.5", "AU"). Normalize at query time, not on write.';
