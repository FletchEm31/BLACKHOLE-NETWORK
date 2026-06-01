#!/usr/bin/env node
// ebay-sold-load-v8.js — load BHN V8 sold-comps CSVs into ebay_transactions (eventhorizon, LA).
//
// Why a V8-specific loader (vs ebay-sold-load.js): the V8 scrape format renamed/added columns
// (title_raw, sale_type, sold_date, seller_username, seller_feedback_score, condition,
// auth_guarantee) and the old loader reads the legacy manual-template names (title,
// transaction_type, created_at→sold_at). The small pure helpers below mirror ebay-sold-load.js
// (extractItemId/parseMoney/parseIntOrNull/parseBool/parsePercent) since that file does not
// export them.
//
// Pipeline: read N CSVs (in order) → derive item_id from listing_url → dedup on item_id (keep
// first) → coerce values to satisfy live CHECK constraints → INSERT ... ON CONFLICT(item_id)
// DO NOTHING. Idempotent; safe to re-run.
//
// Usage:
//   sudo -u postgres node ebay-sold-load-v8.js <csv...> [--dry-run] [--out combined.csv]
//                        [--host /var/run/postgresql] [--db eventhorizon] [--user postgres]
// Default connection is the local unix socket as the postgres superuser (peer auth, no password).

'use strict';

const fs = require('fs');
const { Client } = require('pg');
const { parse } = require('csv-parse/sync');

// ── CLI ──────────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const flag = (k, d = null) => { const i = argv.indexOf(k); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };
const DRY_RUN  = argv.includes('--dry-run');
const OUT_CSV  = flag('--out');
const PG_HOST  = flag('--host', process.env.PGHOST || '/var/run/postgresql');
const PG_DB    = flag('--db',   process.env.PGDATABASE || 'eventhorizon');
const PG_USER  = flag('--user', process.env.PGUSER || 'postgres');
const PG_PORT  = parseInt(flag('--port', process.env.PGPORT || '5432'), 10);
const CSV_PATHS = argv.filter((a, i) => !a.startsWith('--') && (i === 0 || argv[i - 1] !== '--out' && argv[i - 1] !== '--host' && argv[i - 1] !== '--db' && argv[i - 1] !== '--user' && argv[i - 1] !== '--port'));

if (CSV_PATHS.length === 0) { console.error('No CSV paths given.'); process.exit(1); }

// ── Helpers (mirror ebay-sold-load.js:36–115) ─────────────────────────────────
function extractItemId(url) {
  if (!url) return null;
  const m = String(url).trim().match(/\/itm\/(\d+)/);
  return m ? m[1] : null;
}
function parseMoney(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const n = parseFloat(String(raw).trim().replace(/^\$/, '').replace(/,/g, ''));
  return isNaN(n) ? null : n;
}
function parsePercent(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const n = parseFloat(String(raw).trim().replace(/%$/, ''));
  return isNaN(n) ? null : n;
}
function parseIntOrNull(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const n = parseInt(String(raw).trim().replace(/,/g, ''), 10);
  return isNaN(n) ? null : n;
}
function parseBool(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const s = String(raw).trim().toLowerCase();
  if (['true', 'yes', '1'].includes(s)) return true;
  if (['false', 'no', '0'].includes(s)) return false;
  return null;
}
function nz(raw) {  // empty string → null, else trimmed string
  if (raw === null || raw === undefined) return null;
  const s = String(raw).trim();
  return s === '' ? null : s;
}

// V8 sold_date is free text like "Sold May 30, 2026" → YYYY-MM-DD (no Date()/TZ to avoid off-by-one).
const MONTHS = { jan: '01', feb: '02', mar: '03', apr: '04', may: '05', jun: '06', jul: '07', aug: '08', sep: '09', oct: '10', nov: '11', dec: '12' };
function parseSoldDate(raw) {
  if (!raw) return null;
  const s = String(raw).trim().replace(/^sold\s+/i, '');
  const m = s.match(/^([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})$/);
  if (!m) return null;
  const mon = MONTHS[m[1].slice(0, 3).toLowerCase()];
  if (!mon) return null;
  return `${m[3]}-${mon}-${String(m[2]).padStart(2, '0')}`;
}

// Constraint-conforming coercions (allowed sets verified against live pg_constraint).
const EDITIONS   = new Set(['1st Edition', 'Unlimited', 'Shadowless', 'N/A']);
const GRADERS    = new Set(['PSA', 'CGC', 'BGS', 'SGC']);
const PRINT_VARS = new Set(['Standard', 'Holo', 'Error', 'No Symbol', 'W Stamp', 'Winner', 'Jumbo', 'Prerelease', 'Gold Border', 'Red Cheeks', 'WB Movie', 'Nintendo Power', 'WOTC', '1999-2000 Copyright']);
const SALE_TYPES = new Set(['fixed_price', 'auction', 'offer_accepted', 'buyback', 'peer_to_peer']);

// ebay_transactions has FK (grader, grade) → master_grade_catalog(grader, raw_label), MATCH SIMPLE
// (a NULL grader OR NULL grade is exempt). validGrades is loaded from the live catalog at runtime.
// Normalize a raw grade to a catalog raw_label; if impossible, return null grade (FK exempt) and
// the caller stashes the original in raw_payload. Mirrors ebay-sold-load.js:122 normalizeGrade.
function normalizeGrade(grader, grade, valid) {
  if (!grader || !grade) return { grade: grade || null, normalized: false };
  if (valid.has(`${grader}|${grade}`)) return { grade, normalized: false };
  // Numeric cleanup: "10."→"10", "9.0"→"9", "8."→"8" (JS String() drops trailing .0 and bare dot).
  const f = parseFloat(grade);
  if (!isNaN(f)) {
    const cand = String(f);
    if (valid.has(`${grader}|${cand}`)) return { grade: cand, normalized: true, orig: grade };
  }
  return { grade: null, rejected: true, orig: grade };  // keep grader, NULL grade → FK exempt
}

// ── Read + combine + dedup ────────────────────────────────────────────────────
const counters = { read: 0, noId: 0, dupInBatch: 0, edBlank: 0, graderBlank: 0, graderCoerced: 0, saleCoerced: 0, saleNulled: 0, pvCoerced: 0, gradeNormalized: 0, gradeNulled: 0 };
const seen = new Map();        // item_id → original row (first wins)
const combinedRows = [];       // deduped original rows (for --out provenance)
let header = null;

for (const p of CSV_PATHS) {
  const text = fs.readFileSync(p, 'utf8');
  const recs = parse(text, { columns: true, skip_empty_lines: true, trim: true, bom: true, relax_column_count: true });
  if (!header && recs.length) header = Object.keys(recs[0]);
  for (const row of recs) {
    counters.read++;
    const itemId = extractItemId(row.listing_url) || nz(row.item_id);
    if (!itemId) { counters.noId++; continue; }
    if (seen.has(itemId)) { counters.dupInBatch++; continue; }
    seen.set(itemId, { itemId, row });
    combinedRows.push(row);
  }
}

// Build mapped records with coercion + coercion accounting.
const mapped = [];
for (const { itemId, row } of seen.values()) {
  // edition
  let edition = nz(row.edition);
  if (edition === null) counters.edBlank++;
  else if (!EDITIONS.has(edition)) { /* none expected; null + record below */ }
  const origEdition = edition;
  if (edition !== null && !EDITIONS.has(edition)) { edition = null; }

  // grader
  let grader = nz(row.grader);
  const origGrader = grader;
  if (grader === null) counters.graderBlank++;
  else {
    grader = grader.toUpperCase();
    if (!GRADERS.has(grader)) { counters.graderCoerced++; grader = null; }  // e.g. TAG
  }

  // print_variant (NOT NULL)
  let pv = nz(row.print_variant) || 'Standard';
  if (!PRINT_VARS.has(pv)) { counters.pvCoerced++; pv = 'Standard'; }

  // sale_type
  let saleRaw = nz(row.sale_type);
  let sale = saleRaw ? saleRaw.toLowerCase() : null;
  if (sale === 'best_offer') { sale = 'offer_accepted'; counters.saleCoerced++; }
  if (sale !== null && !SALE_TYPES.has(sale)) { counters.saleNulled++; sale = null; }

  // raw_payload kept as an object here; grade normalization (needs the live catalog) and
  // JSON.stringify happen in main() after the DB connection is open.
  const rawPayload = { source: 'ebay-sold-load-v8.js', loaded_at: new Date().toISOString(), listing_url: nz(row.listing_url) };
  if (origGrader && grader === null) rawPayload.original_grader = origGrader;
  if (saleRaw && saleRaw.toLowerCase() !== sale) rawPayload.original_sale_type = saleRaw;
  if (origEdition && edition === null) rawPayload.original_edition = origEdition;

  mapped.push({
    item_id: itemId,
    title_raw: nz(row.title_raw),
    card_name: nz(row.card_name),
    set_name: nz(row.set_name),
    card_number: nz(row.card_number),       // kept raw (e.g. "50/64") per plan
    edition,
    print_variant: pv,
    grader,
    grade: nz(row.grade),
    sold_price: parseMoney(row.sold_price),
    shipping: parseMoney(row.shipping),
    sold_date: parseSoldDate(row.sold_date),
    bid_count: parseIntOrNull(row.bid_count),
    sale_type: sale,
    seller_username: nz(row.seller_username),
    seller_feedback_pct: parsePercent(row.seller_feedback_pct),
    seller_feedback_score: parseIntOrNull(row.seller_feedback_score),
    location: nz(row.location),
    condition: nz(row.condition),
    auth_guarantee: parseBool(row.auth_guarantee),
    raw_payload: rawPayload,   // object; normalized + stringified in main()
  });
}

// Optional combined-dedup CSV (original columns, deduped union) for provenance.
if (OUT_CSV && header) {
  const esc = (v) => { if (v === null || v === undefined) return ''; const s = String(v); return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const lines = [header.join(',')];
  for (const r of combinedRows) lines.push(header.map((h) => esc(r[h])).join(','));
  fs.writeFileSync(OUT_CSV, lines.join('\n') + '\n');
  console.log(`Wrote combined-dedup CSV (${combinedRows.length} rows) → ${OUT_CSV}`);
}

function report(extra = {}) {
  console.log('\n── V8 load report ───────────────────────────────');
  console.log(`  CSV files        : ${CSV_PATHS.length}`);
  console.log(`  Rows read        : ${counters.read}`);
  console.log(`  Skipped (no id)  : ${counters.noId}`);
  console.log(`  Dupes in batch   : ${counters.dupInBatch}`);
  console.log(`  Unique to load   : ${mapped.length}`);
  console.log(`  edition blank→NULL    : ${counters.edBlank}`);
  console.log(`  grader blank→NULL     : ${counters.graderBlank}`);
  console.log(`  grader unknown→NULL   : ${counters.graderCoerced}  (orig kept in raw_payload)`);
  console.log(`  sale_type best_offer→offer_accepted : ${counters.saleCoerced}`);
  console.log(`  sale_type unknown→NULL: ${counters.saleNulled}`);
  console.log(`  print_variant→Standard: ${counters.pvCoerced}`);
  console.log(`  grade normalized (e.g. "10."→"10") : ${counters.gradeNormalized}`);
  console.log(`  grade →NULL (not in catalog)       : ${counters.gradeNulled}  (orig kept in raw_payload)`);
  for (const [k, v] of Object.entries(extra)) console.log(`  ${k} : ${v}`);
  console.log('──────────────────────────────────────────────────');
}

// ── DB ─────────────────────────────────────────────────────────────────────────
async function main() {
  const client = new Client({ host: PG_HOST, database: PG_DB, user: PG_USER, port: PG_PORT, password: process.env.PGPASSWORD });
  await client.connect();
  console.log(`Connected to ${PG_DB} as ${PG_USER} via ${PG_HOST}.`);

  // Which target columns actually exist (robust if ALTER hasn't been run).
  const colRes = await client.query("SELECT column_name FROM information_schema.columns WHERE table_name='ebay_transactions'");
  const liveCols = new Set(colRes.rows.map((r) => r.column_name));
  const desired = ['item_id', 'title_raw', 'card_name', 'set_name', 'card_number', 'edition', 'print_variant', 'grader', 'grade', 'sold_price', 'shipping', 'sold_date', 'bid_count', 'sale_type', 'seller_username', 'seller_feedback_pct', 'seller_feedback_score', 'location', 'condition', 'auth_guarantee', 'raw_payload'];
  const useCols = desired.filter((c) => liveCols.has(c));
  const missing = desired.filter((c) => !liveCols.has(c));
  if (missing.length) console.log(`  [note] target columns missing (will not be loaded): ${missing.join(', ')}`);

  // Grade FK: load valid (grader, raw_label) pairs from the live catalog, then normalize each
  // row's grade so no row violates sold_listings_grade_fk (unfixable → NULL grade, FK-exempt).
  const gradeRes = await client.query('SELECT grader, raw_label FROM master_grade_catalog');
  const validGrades = new Set(gradeRes.rows.map((r) => `${r.grader}|${r.raw_label}`));
  for (const m of mapped) {
    const ng = normalizeGrade(m.grader, m.grade, validGrades);
    if (ng.normalized) { counters.gradeNormalized++; m.raw_payload.original_grade = ng.orig; }
    else if (ng.rejected) { counters.gradeNulled++; m.raw_payload.original_grade = ng.orig; }
    m.grade = ng.grade;
  }

  if (DRY_RUN) {
    report({ 'would use columns': useCols.length, 'sample': '' });
    console.log('  Sample mapped rows:');
    for (const m of mapped.slice(0, 5)) console.log('   ', JSON.stringify({ item_id: m.item_id, card_name: m.card_name, set: m.set_name, '#': m.card_number, ed: m.edition, pv: m.print_variant, grader: m.grader, grade: m.grade, price: m.sold_price, sold_date: m.sold_date, sale: m.sale_type, seller: m.seller_username, cond: m.condition }));
    await client.end();
    console.log('\n  [DRY-RUN] No writes performed.');
    return;
  }

  const placeholders = useCols.map((_, i) => `$${i + 1}`).join(',');
  const sql = `INSERT INTO ebay_transactions (${useCols.join(',')}) VALUES (${placeholders}) ON CONFLICT (item_id) DO NOTHING`;

  let inserted = 0, skipped = 0, errors = 0;
  await client.query('BEGIN');
  for (const m of mapped) {
    const vals = useCols.map((c) => (c === 'raw_payload' ? JSON.stringify(m[c]) : m[c]));
    try {
      const r = await client.query(sql, vals);
      if (r.rowCount === 0) skipped++; else inserted++;
    } catch (e) {
      errors++;
      if (errors <= 20) console.error(`  [ERROR] item_id=${m.item_id}: ${e.message}`);
    }
  }
  if (errors === 0) { await client.query('COMMIT'); }
  else { await client.query('ROLLBACK'); console.error(`\n  ${errors} row error(s) → transaction ROLLED BACK (no rows committed). Fix and re-run.`); }
  await client.end();

  report({ inserted, 'skipped (dup/conflict)': skipped, errors });
  if (errors === 0) console.log('  COMMIT ok.');
}

main().catch((e) => { console.error('fatal:', e); process.exit(1); });
