-- Live Temperature Trajectory feature (2026-07-20) -- grants for the
-- weatherbhn_dashboard role.
--
-- /api/live-trajectory reads two tables this role has never had SELECT on:
--   - weather_bronze_nws_forecast_snapshots
--   - weather_bronze_openmeteo_forecast_snapshots
--
-- weather_bronze_synoptic_asos is re-asserted defensively below even though
-- it was confirmed already granted (2026-07-19, during the Active Trade
-- Summary build) -- verified live via has_table_privilege() before writing
-- this migration: asos=true, nws=false, gfs=false. Re-running a GRANT that
-- already exists is a no-op, so this is safe to run either way and keeps
-- the full set of tables this feature depends on documented in one place.

GRANT SELECT ON weather_bronze_synoptic_asos TO weatherbhn_dashboard;
GRANT SELECT ON weather_bronze_nws_forecast_snapshots TO weatherbhn_dashboard;
GRANT SELECT ON weather_bronze_openmeteo_forecast_snapshots TO weatherbhn_dashboard;
