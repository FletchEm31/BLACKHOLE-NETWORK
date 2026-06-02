'use strict';
// ebay-json-load.js — load Chrome-MCP scraped JSON files into ebay_transactions.
// Fields in source JSON: item_id, title_raw, sold_date, sold_price, sale_type, bid_count,
// shipping, listing_url. All other columns (card_name, set_name, grader, etc.) are NULL
// and populated later by ebay-title-reparse.js.
// Idempotent: ON CONFLICT(item_id) DO NOTHING.
//
// Usage: sudo -u postgres node ebay-json-load.js file1.json file2.json ...

const fs = require('fs');
const { Client } = require('pg');

const files = process.argv.slice(2);
if (!files.length) { console.error('No JSON files given.'); process.exit(1); }

function parseMoney(v) {
  if (v == null) return null;
  const n = parseFloat(String(v).replace(/[^0-9.]/g, ''));
  return isNaN(n) ? null : n;
}
function parseIntOrNull(v) {
  const n = parseInt(v, 10);
  return isNaN(n) ? null : n;
}
const SALE_TYPE_MAP = {
  fixed_price: 'fixed_price', bin: 'fixed_price', 'buy it now': 'fixed_price',
  auction: 'auction',
  best_offer: 'offer_accepted', offer_accepted: 'offer_accepted',
  buyback: 'buyback', peer_to_peer: 'peer_to_peer',
};
function normSaleType(v) {
  if (!v) return null;
  return SALE_TYPE_MAP[String(v).toLowerCase().trim()] || null;
}

const SQL = [
  'INSERT INTO ebay_transactions',
  '  (item_id, title_raw, sold_at, sold_date, sold_price, currency, shipping,',
  '   sale_type, bid_count, seller_username, seller_feedback_pct, raw_payload)',
  'VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)',
  'ON CONFLICT (item_id) DO NOTHING',
].join('\n');

(async () => {
  const client = new Client({
    host: process.env.PGHOST || '/var/run/postgresql',
    database: process.env.PGDATABASE || 'eventhorizon',
    user: process.env.PGUSER || 'postgres',
    port: parseInt(process.env.PGPORT || '5432', 10),
  });
  await client.connect();
  console.log('Connected to eventhorizon.');

  let inserted = 0, skipped = 0, errors = 0;

  for (const f of files) {
    let rows;
    try {
      rows = JSON.parse(fs.readFileSync(f, 'utf8'));
    } catch (e) {
      console.error(`Failed to parse ${f}: ${e.message}`);
      errors++;
      continue;
    }
    console.log(`${f}: ${rows.length} rows`);
    for (const r of rows) {
      try {
        const soldAt   = r.sold_date || null;
        const soldDate = soldAt ? soldAt.slice(0, 10) : null;
        const res = await client.query(SQL, [
          r.item_id                        || null,  // $1  item_id
          r.title_raw                      || null,  // $2  title_raw
          soldAt,                                    // $3  sold_at (timestamp)
          soldDate,                                  // $4  sold_date (date)
          parseMoney(r.sold_price),                  // $5  sold_price
          'USD',                                     // $6  currency
          parseMoney(r.shipping),                    // $7  shipping
          normSaleType(r.sale_type),                 // $8  sale_type
          parseIntOrNull(r.bid_count),               // $9  bid_count
          r.seller_username                || null,  // $10 seller_username
          parseMoney(r.seller_feedback_pct),         // $11 seller_feedback_pct
          JSON.stringify({ source: 'chrome-mcp', listing_url: r.listing_url || null }), // $12
        ]);
        res.rowCount > 0 ? inserted++ : skipped++;
      } catch (e) {
        console.error('ERR item_id=' + r.item_id, e.message);
        errors++;
      }
    }
  }

  await client.end();
  console.log('\n── JSON load report ──────────────────────');
  console.log('Inserted:     ', inserted);
  console.log('Skipped (dup):', skipped);
  console.log('Errors:       ', errors);
  console.log('Total:        ', inserted + skipped + errors);
})();
