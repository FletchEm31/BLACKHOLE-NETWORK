-- BHN external data ingestion layer.
-- Stores polled API responses (news, weather) so HORIZON has historical
-- external context, not just current snapshots.
--
-- Populated by:
--   - eh-news-poll workflow (every 2h, NewsAPI top-headlines)
--   - eh-weather-poll workflow (every 30 min, OpenWeatherMap onecall)
--
-- Read by:
--   - HORIZON's query_db tool (agent-driven recall)
--   - M2 Morning Briefing (when wired)
--   - Future analytical queries (trends in news topics, weather extremes, etc.)
--
-- Privacy: all data here is publicly-published external information (news
-- headlines, weather observations). No PII, no operator data. Living on the
-- encrypted NVMe is defense-in-depth, not requirement.
--
-- Retention: 7 days hot per existing operator policy. eh-purge cron prunes
-- rows older than 7 days. Consider archiving notable items (extreme weather,
-- significant news) to memories before pruning.
--
-- To apply:
--   psql -d eventhorizon -f external-data-schema.sql
--
-- Pre-req: pgvector extension already installed (memories-schema.sql).

CREATE EXTENSION IF NOT EXISTS vector;

-- ----- news_articles -----
-- One row per article fetched from NewsAPI. UNIQUE on article_url so re-polls
-- don't duplicate. published_at preserved separately from fetched_at because
-- articles can be days old when first polled.
CREATE TABLE IF NOT EXISTS news_articles (
    id              BIGSERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL DEFAULT 'newsapi',
    article_url     TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    source_name     TEXT,                       -- e.g. "Reuters", "AP", "CNN"
    author          TEXT,
    published_at    TIMESTAMPTZ,
    category        TEXT,                       -- general, business, tech, etc.
    country         TEXT DEFAULT 'us',
    embedding       vector(384),                -- nullable; populated lazily
    raw_payload     JSONB,
    UNIQUE (article_url)
);

CREATE INDEX IF NOT EXISTS news_articles_fetched_idx   ON news_articles (fetched_at DESC);
CREATE INDEX IF NOT EXISTS news_articles_published_idx ON news_articles (published_at DESC);
CREATE INDEX IF NOT EXISTS news_articles_source_idx    ON news_articles (source_name);
CREATE INDEX IF NOT EXISTS news_articles_embedding_idx
    ON news_articles USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding IS NOT NULL;

-- ----- weather_snapshots -----
-- One row per OpenWeatherMap fetch. Stores current + today's daily forecast
-- summary so HORIZON can answer "what's the weather" or "did it rain today"
-- without re-calling the API.
CREATE TABLE IF NOT EXISTS weather_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    fetched_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lat                     NUMERIC(7,4) NOT NULL,
    lon                     NUMERIC(8,4) NOT NULL,
    location_label          TEXT,               -- e.g. "operator-home"
    current_temp_f          NUMERIC(5,1),
    feels_like_f            NUMERIC(5,1),
    today_high_f            NUMERIC(5,1),
    today_low_f             NUMERIC(5,1),
    conditions              TEXT,               -- "clear sky", "light rain", etc.
    summary                 TEXT,               -- daily summary string
    precipitation_chance    NUMERIC(4,2),       -- 0.00 - 1.00
    wind_mph                NUMERIC(5,1),
    humidity_pct            INTEGER,
    uv_index                NUMERIC(4,2),
    sunrise                 TIMESTAMPTZ,
    sunset                  TIMESTAMPTZ,
    raw_payload             JSONB
);

CREATE INDEX IF NOT EXISTS weather_snapshots_fetched_idx ON weather_snapshots (fetched_at DESC);
CREATE INDEX IF NOT EXISTS weather_snapshots_location_idx ON weather_snapshots (location_label, fetched_at DESC);

-- Grant n8n_user (used by all workflow Postgres credentials) full access to
-- both tables. INSERT for the polling workflows, SELECT for HORIZON's reads.
GRANT SELECT, INSERT, UPDATE, DELETE ON news_articles, weather_snapshots TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE news_articles_id_seq, weather_snapshots_id_seq TO n8n_user;

-- HORIZON's read-only credential should also be able to SELECT from these.
-- The agent_read role (or whatever the read-only n8n credential maps to)
-- needs SELECT.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_readonly') THEN
    GRANT SELECT ON news_articles, weather_snapshots TO n8n_readonly;
  END IF;
END $$;
