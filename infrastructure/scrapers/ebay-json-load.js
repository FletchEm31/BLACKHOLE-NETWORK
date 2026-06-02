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

const SQL = [
  'INSERT INTO ebay_transactions',
  '  (item_id, title_raw, sold_at, sold_date, sold_price, currency, shipping,',
  '   sale_type, bid_count, raw_payload)',
  'VALUES ($1,$2,$3,$3::date,$4,$5,$6,$7,$8,$9)',
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
        const res = await client.query(SQL, [
          r.item_id                   || null,
          r.title_raw                 || null,
          r.sold_date                 || null,
          parseMoney(r.sold_price),
          'USD',
          parseMoney(r.shipping),
          r.sale_type                 || null,
          parseIntOrNull(r.bid_count),
          JSON.stringify({ source: 'chrome-mcp', listing_url: r.listing_url || null }),
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
