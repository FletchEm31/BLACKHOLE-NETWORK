#!/usr/bin/env node
// Loads a BHN sold-comps CSV into ebay_transactions (eventhorizon DB on LA).
// Validates grade FK against live master_grade_catalog at startup.
// Grade failures → grade_reject_log (never crashes the batch).
// Upserts on item_id — safe to re-run.
//
// Usage:
//   PGPASSWORD=xxx node ebay-sold-load.js [--csv <path>] [--host <host>] [--dry-run]
//
// CSV is expected to have the columns produced by the BHN manual scrape template.
// item_id is extracted from listing_url (avoids scientific-notation precision loss from Excel).

'use strict';

const fs = require('fs');
const path = require('path');
const { Client } = require('pg');
const { parse } = require('csv-parse/sync');

// ── CLI flags ──────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const flag = (k) => { const i = args.indexOf(k); return i >= 0 && args[i + 1] ? args[i + 1] : null; };
const hasFlag = (k) => args.includes(k);

const CSV_PATH  = flag('--csv')  || path.join(__dirname, 'data', 'BHN_sold_comp scrape.csv');
const DRY_RUN   = hasFlag('--dry-run');
const PG_HOST   = flag('--host') || process.env.PGHOST     || '10.8.0.1';
const PG_DB     = flag('--db')   || process.env.PGDATABASE || 'eventhorizon';
const PG_USER   = flag('--user') || process.env.PGUSER     || 'postgres';
const PG_PORT   = parseInt(flag('--port') || process.env.PGPORT || '5432', 10);

// ── Helpers ────────────────────────────────────────────────────────────────────

// Extract eBay item ID from listing URL — avoids scientific-notation precision loss
// from Excel CSV export (e.g. 2.98329E+11 loses digits; URL is always exact).
function extractItemId(listingUrl) {
  if (!listingUrl) return null;
  const m = String(listingUrl).trim().match(/\/itm\/(\d+)/);
  return m ? m[1] : null;
}

// Handle Excel date auto-format for card_number: Excel converts 8/82 → Aug-82.
// Maps month abbreviation back to card number integer string.
const MONTH_TO_NUM = {
  jan: '1', feb: '2', mar: '3', apr: '4', may: '5', jun: '6',
  jul: '7', aug: '8', sep: '9', oct: '10', nov: '11', dec: '12',
};
function cleanCardNumber(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  // Excel date format: Aug-82, Jan-82, etc.
  const dateMatch = s.match(/^([A-Za-z]{3})-\d+$/);
  if (dateMatch) {
    const num = MONTH_TO_NUM[dateMatch[1].toLowerCase()];
    return num || null;
  }
  // Strip leading '#', take numerator before '/', strip leading zeros
  const cleaned = s.replace(/^#/, '').split('/')[0].trim();
  const parsed = parseInt(cleaned, 10);
  return isNaN(parsed) ? cleaned : String(parsed);
}

// Strip '$' and parse to float; return null for empty/non-numeric.
function parseMoney(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const s = String(raw).trim().replace(/^\$/, '').replace(/,/g, '');
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}

// Coerce empty string to null; parse int.
function parseIntOrNull(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const n = parseInt(String(raw).replace(/,/g, ''), 10);
  return isNaN(n) ? null : n;
}

// Coerce returns_accepted to boolean.
function parseBool(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const s = String(raw).trim().toLowerCase();
  if (s === 'true' || s === 'yes' || s === '1') return true;
  if (s === 'false' || s === 'no' || s === '0') return false;
  return null;
}

// Parse a timestamp string into ISO format; return null if unparseable.
function parseDate(raw) {
  if (!raw || raw.trim() === '') return null;
  const d = new Date(raw.trim());
  return isNaN(d.getTime()) ? null : d.toISOString();
}

// Normalize grade for FK validation.
// The seed CSV stores PSA grades as tier names (e.g. "Gem Mint 10") but master_grade_catalog
// uses bare numbers for PSA ("10"). CGC grades 1–9 are also bare numbers in the catalog.
// For CGC bare "10" (ambiguous — could be Perfect/Pristine/Gem Mint 10) → do NOT normalize;
// let it fall through to grade_reject_log.
function normalizeGrade(grader, grade, validGrades) {
  if (!grader || !grade) return { grader, grade, normalized: false };
  if (validGrades.has(`${grader}|${grade}`)) return { grader, grade, normalized: false };

  // Extract trailing decimal number from grade label (e.g. "Gem Mint 10" → "10", "Mint+ 9.5" → "9.5")
  const numMatch = grade.match(/(\d+\.?\d*)\s*$/);
  if (numMatch) {
    const bare = numMatch[1];
    // CGC bare 10 is ambiguous across Perfect/Pristine/Gem Mint tiers — do not normalize
    if (grader === 'CGC' && bare === '10') return { grader, grade, normalized: false };
    if (validGrades.has(`${grader}|${bare}`)) {
      return { grader, grade: bare, normalized: true, originalGrade: grade };
    }
  }
  return { grader, grade, normalized: false };
}

// ── Main ───────────────────────────────────────────────────────────────────────
async function main() {
  if (!fs.existsSync(CSV_PATH)) {
    console.error(`CSV not found: ${CSV_PATH}`);
    process.exit(1);
  }

  const csvText = fs.readFileSync(CSV_PATH, 'utf8');
  const records = parse(csvText, {
    columns: true,
    skip_empty_lines: true,
    trim: true,
    bom: true,
  });
  console.log(`Parsed ${records.length} rows from CSV.`);

  if (DRY_RUN) {
    console.log('[DRY-RUN] Connecting to DB to validate grades — no writes will occur.');
  }

  const client = new Client({
    host: PG_HOST,
    database: PG_DB,
    user: PG_USER,
    password: process.env.PGPASSWORD,
    port: PG_PORT,
  });

  await client.connect();
  console.log(`Connected to ${PG_HOST}/${PG_DB}.`);

  // Load valid (grader, raw_label) pairs from live DB — ground truth for FK validation.
  const catalogRes = await client.query(
    'SELECT grader, raw_label FROM master_grade_catalog ORDER BY grader, numeric_grade DESC NULLS LAST'
  );
  const validGrades = new Set(catalogRes.rows.map((r) => `${r.grader}|${r.raw_label}`));
  console.log(`Loaded ${catalogRes.rows.length} grade catalog entries.`);

  // Detect whether the seller column is 'seller' or 'seller_username'.
  const colRes = await client.query(`
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'ebay_transactions' AND column_name IN ('seller','seller_username')
  `);
  const sellerCol = colRes.rows.find((r) => r.column_name === 'seller_username')
    ? 'seller_username'
    : 'seller';

  // Detect whether grade_label column exists on ebay_transactions.
  const gradeLabelRes = await client.query(`
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'ebay_transactions' AND column_name = 'grade_label'
  `);
  const hasGradeLabel = gradeLabelRes.rows.length > 0;

  if (!DRY_RUN) {
    // Create grade_reject_log if it doesn't exist.
    await client.query(`
      CREATE TABLE IF NOT EXISTS grade_reject_log (
        grader    TEXT,
        raw_label TEXT,
        item_id   TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);
  }

  let inserted    = 0;
  let skipped     = 0;
  let rejected    = 0;
  let normalized  = 0;
  let errors      = 0;

  for (const row of records) {
    // item_id: always extract from listing_url to avoid scientific-notation precision loss.
    const itemIdFromUrl = extractItemId(row.listing_url);
    const itemId = itemIdFromUrl || row.item_id || null;
    if (!itemId) {
      console.warn(`  [SKIP] Row missing item_id and no listing_url: title="${row.title}"`);
      skipped++;
      continue;
    }

    const rawGrader = row.grader ? String(row.grader).trim() || null : null;
    const rawGrade  = row.grade  ? String(row.grade).trim()  || null : null;
    const gradeLabel = row.grade_label ? String(row.grade_label).trim() || null : null;

    // Normalize grade: PSA uses bare numbers ("10") but CSV may have tier names ("Gem Mint 10").
    const norm = normalizeGrade(rawGrader, rawGrade, validGrades);
    const grader = norm.grader;
    const grade  = norm.grade;
    if (norm.normalized) {
      normalized++;
    }

    // Grade FK check: NULL grader/grade passes (MATCH SIMPLE skips NULLs).
    const gradeKey = grader && grade ? `${grader}|${grade}` : null;
    if (gradeKey && !validGrades.has(gradeKey)) {
      if (!DRY_RUN) {
        try {
          await client.query(
            'INSERT INTO grade_reject_log (grader, raw_label, item_id) VALUES ($1, $2, $3)',
            [grader, grade, itemId]
          );
        } catch (e) {
          console.warn(`  [WARN] grade_reject_log insert failed for ${itemId}: ${e.message}`);
        }
      }
      console.log(`  [REJECT] item_id=${itemId} grader=${grader} grade="${grade}" — not in catalog`);
      rejected++;
      continue;
    }

    const soldPrice  = parseMoney(row.sold_price);
    const shipping   = parseMoney(row.shipping);
    const bidCount   = parseIntOrNull(row.bid_count);
    const sellerFb   = parseIntOrNull(row.seller_feedback);
    const watchers   = parseIntOrNull(row.watchers);
    const createdAt  = parseDate(row.created_at);
    const cardNumber = cleanCardNumber(row.card_number);
    const listingUrl = row.listing_url ? row.listing_url.trim() : null;

    if (DRY_RUN) {
      console.log(
        `  [DRY] ${itemId} | ${grader || 'RAW'} ${grade || ''} | $${soldPrice} | ${row.set_name} #${cardNumber}`
      );
      inserted++;
      continue;
    }

    // Build column/value lists dynamically to handle optional grade_label column.
    const cols = [
      'pbds_code','item_id','title','card_name','set_name','card_number','edition',
      'print_variant','grader','grade','sold_price','currency','shipping',
      'transaction_type','bid_count','created_at', sellerCol,'seller_feedback',
      'seller_feedback_pct','cert_number','location','condition','returns_accepted',
      'obo_min_price','current_bid','watchers','listing_url',
    ];
    const vals = [
      row.pbds_code || null,
      itemId,
      row.title     || null,
      row.card_name || null,
      row.set_name  || null,
      cardNumber,
      row.edition   || null,
      row.print_variant || null,
      grader,
      grade,
      soldPrice,
      row.currency  || null,
      shipping,
      row.transaction_type || null,
      bidCount,
      createdAt,
      row.seller    || null,
      sellerFb,
      row.seller_feedback_pct || null,
      null,   // cert_number — Option A: always NULL
      row.location  || null,
      row.condition || null,
      parseBool(row.returns_accepted),
      parseMoney(row.obo_min_price),
      parseMoney(row.current_bid),
      watchers,
      listingUrl,
    ];

    if (hasGradeLabel) {
      cols.push('grade_label');
      vals.push(gradeLabel);
    }

    const placeholders = vals.map((_, i) => `$${i + 1}`).join(',');
    const sql = `INSERT INTO ebay_transactions (${cols.join(',')}) VALUES (${placeholders}) ON CONFLICT (item_id) DO NOTHING`;

    try {
      const result = await client.query(sql, vals);
      if (result.rowCount === 0) {
        skipped++;
      } else {
        inserted++;
      }
    } catch (e) {
      console.error(`  [ERROR] item_id=${itemId}: ${e.message}`);
      errors++;
    }
  }

  await client.end();

  console.log('\n── Load report ─────────────────────────────────');
  console.log(`  Rows processed : ${records.length}`);
  console.log(`  Inserted       : ${inserted}`);
  console.log(`  Skipped (dup)  : ${skipped}`);
  console.log(`  Normalized     : ${normalized}  (tier-label → bare number)`);
  console.log(`  Rejected (FK)  : ${rejected}`);
  if (errors) console.log(`  Errors         : ${errors}`);
  if (DRY_RUN) console.log('\n  [DRY-RUN] No data was written.');
  console.log('────────────────────────────────────────────────');
}

main().catch((e) => {
  console.error('fatal:', e);
  process.exit(1);
});
