-- 2026-06-03 — Fix: GRANT SELECT on fee_schedule to n8n_user
--
-- WHY: courtyard_sales is a view on courtyard_transactions. INSERT into
-- the view fires courtyard_transactions_fill_market_est() trigger which
-- calls compute_market_seller_estimates() which queries fee_schedule.
-- n8n_user had INSERT+UPDATE on fee_schedule but not SELECT, causing
-- "permission denied for table fee_schedule" on every courtyard_sales
-- INSERT since 2026-05-28. No new Courtyard rows were loading for ~6 days.
--
-- APPLIED: 2026-06-03 — verified n8n_user can now INSERT into courtyard_sales.

GRANT SELECT ON fee_schedule TO n8n_user;
