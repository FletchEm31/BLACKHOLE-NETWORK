-- Migration: fix weather_bronze_asos_historic_lax_5min to the full 14-column shape
-- 2026-07-20
--
-- CORRECTION to 2026-07-20-asos-historic-lax-tables.sql. That migration
-- shaped this table with only 4 weather columns (air_temp_f,
-- dew_point_temp_f, wind_speed_kt, wind_direction_deg), based on the three
-- reduced-field files that migration's loader used at the time
-- (ASOS5M-LAX14-11.txt, ASOS5M-LAX17-15.txt, ASOS5M-LAX21-18.txt -- header
-- station,station_name,valid(UTC),tmpf,dwpf,sknt,drct, no gust/precip/
-- pressure). A consolidated file, ASOS5M-LAX26-11-FULL15YR.txt, was
-- sitting in the same operator-staged folder the entire time with the
-- full 14-column header (identical shape to the 1-minute file and to
-- MIA's 5-minute file) and the same 2011-2026 span -- confirmed as a
-- genuine superset replacement, not different data, by reading content
-- directly. It just wasn't noticed until the MIA load surfaced the same
-- full-field 5-min pattern and prompted a second look at LAX's folder.
--
-- This migration adds the 7 missing columns. The table was then TRUNCATEd
-- and reloaded from ASOS5M-LAX26-11-FULL15YR.txt via
-- scripts/weather/asos_historic_lax_loader.py (which now points its 5min
-- config at this one file instead of the three reduced ones) -- run that
-- reload manually after applying this migration, it's not done here.
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20b-asos-historic-lax-5min-full-fields-fix.sql

ALTER TABLE weather_bronze_asos_historic_lax_5min
    ADD COLUMN IF NOT EXISTS gust_direction_deg NUMERIC,
    ADD COLUMN IF NOT EXISTS gust_speed_kt NUMERIC,
    ADD COLUMN IF NOT EXISTS precip_type_code TEXT,
    ADD COLUMN IF NOT EXISTS precip_in NUMERIC,
    ADD COLUMN IF NOT EXISTS pressure_1_inhg NUMERIC,
    ADD COLUMN IF NOT EXISTS pressure_2_inhg NUMERIC,
    ADD COLUMN IF NOT EXISTS pressure_3_inhg NUMERIC;

-- No grant changes needed -- ALTER TABLE ADD COLUMN doesn't affect existing
-- table-level grants, and this table's grants were already asserted in
-- 2026-07-20-asos-historic-lax-tables.sql.

-- Verification checklist (run after applying, before calling this done):
--   1. \d weather_bronze_asos_historic_lax_5min
--      -- should now match weather_bronze_asos_historic_lax_1min's column shape
--   2. TRUNCATE weather_bronze_asos_historic_lax_5min;
--      python3 scripts/weather/asos_historic_lax_loader.py --resolution 5min
--   3. SELECT observed_at, air_temp_f, wind_speed_kt, gust_speed_kt, precip_type_code, pressure_1_inhg
--        FROM weather_bronze_asos_historic_lax_5min ORDER BY observed_at LIMIT 5;
--      -- confirm all columns populated, not just the original 4
