#!/usr/bin/env node
// Master XLSX export of ebay_transactions to BHN_SOLD_COMPS_MASTER.xlsx.
//
// Runs from the operator's Windows machine (R:); connects to LA Postgres
// over WireGuard at 10.8.0.1:5432. Builds a multi-tab workbook:
//   - One "<SETCODE> - Master" tab per set: full historical comps,
//     deduped by item_id (UNIQUE), regenerated each run.
//   - One "<SETCODE> - <YYYY-MM-DD>" daily tab per set: appends today's
//     net-new rows (filter inserted_at::date = today UTC; dedup by item_id
//     against any rows already in the tab from earlier runs same day).
//   - "Summary" tab: per-set counts + latest timestamps + export run info.
//
// Usage (PowerShell):
//   $env:PGPASSWORD = "your-pg-password"
//   node infrastructure/scrapers/export-master.js [--sets TRK,BST] [--out <path>]
//
// Credentials can also live in infrastructure/scrapers/.export-master.env
// (gitignored) with KEY=VALUE lines for PGUSER / PGPASSWORD / PGHOST etc.

'use strict';

const fs = require('fs');
const path = require('path');
const { Client } = require('pg');
const ExcelJS = require('exceljs');

// ── env loader (.export-master.env, simple KEY=VALUE lines, no quoting) ───────
const ENV_PATH = path.join(__dirname, '.export-master.env');
if (fs.existsSync(ENV_PATH)) {
  for (const line of fs.readFileSync(ENV_PATH, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
    const idx = trimmed.indexOf('=');
    const k = trimmed.slice(0, idx).trim();
    const v = trimmed.slice(idx + 1).trim();
    if (!process.env[k]) process.env[k] = v;
  }
}

// ── CLI flags ─────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const flag = (k) => { const i = args.indexOf(k); return i >= 0 && args[i + 1] ? args[i + 1] : null; };

const OUT_PATH = flag('--out') || path.join(__dirname, 'data', 'BHN_SOLD_COMPS_MASTER.xlsx');
const SETS_FILTER = flag('--sets')
  ? flag('--sets').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean)
  : null;

const PG_HOST = process.env.PGHOST     || '10.8.0.1';
const PG_DB   = process.env.PGDATABASE || 'eventhorizon';
const PG_USER = process.env.PGUSER     || 'agent_reader';
const PG_PASS = process.env.PGPASSWORD;
const PG_PORT = parseInt(process.env.PGPORT || '5432', 10);

if (!PG_PASS) {
  console.error('PGPASSWORD not set.');
  console.error('Either set $env:PGPASSWORD before running, or create:');
  console.error(`  ${ENV_PATH}`);
  console.error('with these lines:');
  console.error('  PGUSER=agent_reader');
  console.error('  PGPASSWORD=<the agent_reader password from Proton Pass: EH-PG-agent_reader>');
  process.exit(2);
}

const TODAY_UTC = new Date().toISOString().slice(0, 10);

// ── queries ───────────────────────────────────────────────────────────────────
const QUERY = `
SELECT s.set_code,
       t.card_code, t.item_id, t.title_raw, t.card_name, t.set_name, t.card_number,
       t.edition, t.print_variant, t.grader, t.grade, t.grade_label, t.bhn_slab_id,
       t.sold_price, t.currency, t.shipping, t.sale_type, t.bid_count,
       t.sold_at, t.seller, t.seller_feedback_pct, t.location, t.cert_number,
       t.watchers, t.obo_min_price, t.inserted_at,
       t.raw_payload->>'listing_url'                AS listing_url,
       t.raw_payload->>'condition'                  AS condition_,
       (t.raw_payload->>'returns_accepted')::boolean AS returns_accepted,
       (t.raw_payload->>'current_bid')::numeric     AS current_bid,
       (t.raw_payload->>'seller_feedback')::integer AS seller_feedback
  FROM ebay_transactions t
  LEFT JOIN master_set_catalog s ON s.set_name = t.set_name
 WHERE ($1::text[] IS NULL OR s.set_code = ANY($1))
 ORDER BY s.set_code NULLS LAST, t.sold_at DESC NULLS LAST, t.inserted_at DESC
`;

// Progress / work-order query: every catalog card LEFT JOINed against comp counts.
// Composite-key join (set_name, card_number, edition, print_variant) because
// ebay_transactions.card_id is NULL on legacy rows + on rows loaded via the CSV
// loader (loader writes the composite columns but not card_id).
const CATALOG_QUERY = `
WITH comps AS (
  SELECT set_name, card_number, edition, print_variant, COUNT(*) AS comp_count
    FROM ebay_transactions
   GROUP BY set_name, card_number, edition, print_variant
)
SELECT s.set_code,
       mcc.set_name,
       mcc.card_number,
       mcc.card_name,
       mcc.edition,
       mcc.print_variant,
       mcc.card_code,
       COALESCE(c.comp_count, 0)::int AS comp_count
  FROM master_card_catalog mcc
  JOIN master_set_catalog  s ON s.set_name = mcc.set_name
  LEFT JOIN comps c
    ON c.set_name     = mcc.set_name
   AND c.card_number  = mcc.card_number
   AND c.edition      = mcc.edition
   AND c.print_variant = mcc.print_variant
 WHERE ($1::text[] IS NULL OR s.set_code = ANY($1))
 ORDER BY s.set_code,
          -- natural numeric sort of card_number when it parses as int, else lexical
          CASE WHEN mcc.card_number ~ '^[0-9]+$' THEN LPAD(mcc.card_number, 6, '0') ELSE mcc.card_number END,
          mcc.edition, mcc.print_variant
`;

// ── column definition (used for both Master and Daily tabs) ───────────────────
const COLS = [
  { header: 'item_id',             key: 'item_id',             width: 14 },
  { header: 'card_code',           key: 'card_code',           width: 14 },
  { header: 'card_name',           key: 'card_name',           width: 28 },
  { header: 'set_name',            key: 'set_name',            width: 20 },
  { header: 'card_number',         key: 'card_number',         width: 8  },
  { header: 'edition',             key: 'edition',             width: 12 },
  { header: 'print_variant',       key: 'print_variant',       width: 14 },
  { header: 'grader',              key: 'grader',              width: 6  },
  { header: 'grade',               key: 'grade',               width: 14 },
  { header: 'grade_label',         key: 'grade_label',         width: 14 },
  { header: 'bhn_slab_id',         key: 'bhn_slab_id',         width: 17 },
  { header: 'sold_price',          key: 'sold_price',          width: 11, style: { numFmt: '"$"#,##0.00' } },
  { header: 'currency',            key: 'currency',            width: 8  },
  { header: 'shipping',            key: 'shipping',            width: 10, style: { numFmt: '"$"#,##0.00' } },
  { header: 'sale_type',           key: 'sale_type',           width: 14 },
  { header: 'bid_count',           key: 'bid_count',           width: 9  },
  { header: 'sold_at',             key: 'sold_at',             width: 20 },
  { header: 'seller',              key: 'seller',              width: 18 },
  { header: 'seller_feedback',     key: 'seller_feedback',     width: 14 },
  { header: 'seller_feedback_pct', key: 'seller_feedback_pct', width: 11 },
  { header: 'watchers',            key: 'watchers',            width: 9  },
  { header: 'location',            key: 'location',            width: 18 },
  { header: 'condition',           key: 'condition_',          width: 14 },
  { header: 'returns_accepted',    key: 'returns_accepted',    width: 9  },
  { header: 'current_bid',         key: 'current_bid',         width: 11 },
  { header: 'obo_min_price',       key: 'obo_min_price',       width: 12 },
  { header: 'cert_number',         key: 'cert_number',         width: 14 },
  { header: 'title_raw',           key: 'title_raw',           width: 50 },
  { header: 'listing_url',         key: 'listing_url',         width: 50 },
  { header: 'inserted_at',         key: 'inserted_at',         width: 20 },
];

const SUMMARY_COLS = [
  { header: 'set_code',           key: 'set_code',           width: 10 },
  { header: 'set_name',           key: 'set_name',           width: 22 },
  { header: 'total_rows',         key: 'total_rows',         width: 12 },
  { header: 'rows_today_appended',key: 'rows_today_appended',width: 19 },
  { header: 'rows_today_total',   key: 'rows_today_total',   width: 17 },
  { header: 'latest_sold_at',     key: 'latest_sold_at',     width: 22 },
  { header: 'latest_inserted_at', key: 'latest_inserted_at', width: 22 },
  { header: 'cards_done',         key: 'cards_done',         width: 11 },
  { header: 'cards_partial',      key: 'cards_partial',      width: 13 },
  { header: 'cards_not_started',  key: 'cards_not_started',  width: 17 },
  { header: 'pct_done',           key: 'pct_done',           width: 9, style: { numFmt: '0.0%' } },
];

const PROGRESS_COLS = [
  { header: 'card_number',   key: 'card_number',   width: 11 },
  { header: 'card_name',     key: 'card_name',     width: 28 },
  { header: 'edition',       key: 'edition',       width: 12 },
  { header: 'print_variant', key: 'print_variant', width: 14 },
  { header: 'card_code',     key: 'card_code',     width: 14 },
  { header: 'comp_count',    key: 'comp_count',    width: 11 },
  { header: 'status',        key: 'status',        width: 13 },
];

// Done/Partial/Not Started thresholds + Excel "Good/Neutral/Bad" cell fills
const PROGRESS_DONE_THRESHOLD = 10;
const FILL_GREEN  = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFC6EFCE' } };
const FILL_YELLOW = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFEB9C' } };
const FILL_RED    = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFC7CE' } };
const FONT_GREEN  = { color: { argb: 'FF006100' }, bold: true };
const FONT_YELLOW = { color: { argb: 'FF9C5700' }, bold: true };
const FONT_RED    = { color: { argb: 'FF9C0006' }, bold: true };

function statusForCompCount(n) {
  if (n >= PROGRESS_DONE_THRESHOLD) return { label: 'Done',        fill: FILL_GREEN,  font: FONT_GREEN  };
  if (n >= 1)                       return { label: 'Partial',     fill: FILL_YELLOW, font: FONT_YELLOW };
  return                                   { label: 'Not Started', fill: FILL_RED,    font: FONT_RED    };
}

// ── main ──────────────────────────────────────────────────────────────────────
async function main() {
  console.log(`[export-master] ${PG_USER}@${PG_HOST}:${PG_PORT}/${PG_DB}`);
  const client = new Client({ host: PG_HOST, port: PG_PORT, user: PG_USER, password: PG_PASS, database: PG_DB });
  await client.connect();

  console.log(`[export-master] querying ebay_transactions${SETS_FILTER ? ` (sets: ${SETS_FILTER.join(',')})` : ''}…`);
  const res = await client.query(QUERY, [SETS_FILTER]);
  console.log(`[export-master] fetched ${res.rows.length} rows.`);

  console.log(`[export-master] querying master_card_catalog × comp counts for Progress tabs…`);
  const catalogRes = await client.query(CATALOG_QUERY, [SETS_FILTER]);
  console.log(`[export-master] fetched ${catalogRes.rows.length} catalog rows.`);

  await client.end();

  // Group catalog rows by set_code for per-set Progress tabs
  const catalogBySet = new Map();
  for (const r of catalogRes.rows) {
    const code = r.set_code || 'UNK';
    if (!catalogBySet.has(code)) catalogBySet.set(code, []);
    catalogBySet.get(code).push(r);
  }

  const workbook = new ExcelJS.Workbook();
  let existed = false;
  if (fs.existsSync(OUT_PATH)) {
    try {
      await workbook.xlsx.readFile(OUT_PATH);
      existed = true;
      console.log(`[export-master] loaded existing workbook: ${OUT_PATH}`);
    } catch (e) {
      console.warn(`[export-master] existing workbook unreadable (${e.message}); starting fresh.`);
    }
  }

  // Group rows by set_code
  const bySet = new Map();
  for (const r of res.rows) {
    const code = r.set_code || 'UNK';
    if (!bySet.has(code)) bySet.set(code, []);
    bySet.get(code).push(r);
  }

  const summaryRows = [];

  // Sort sets for deterministic tab order
  const sortedSetCodes = [...bySet.keys()].sort();

  for (const code of sortedSetCodes) {
    const rows = bySet.get(code);

    // Master tab — regenerated each run (full history, deterministic from PG)
    const masterName = `${code} - Master`;
    const existingMaster = workbook.getWorksheet(masterName);
    if (existingMaster) workbook.removeWorksheet(existingMaster.id);
    const master = workbook.addWorksheet(masterName);
    master.columns = COLS;
    master.addRows(rows);
    master.getRow(1).font = { bold: true };
    master.views = [{ state: 'frozen', ySplit: 1 }];

    // Daily tab — append-only, dedup by item_id
    const dailyName = `${code} - ${TODAY_UTC}`;
    let daily = workbook.getWorksheet(dailyName);
    const existingItemIds = new Set();
    if (daily) {
      const itemIdCol = daily.getColumn('item_id');
      if (itemIdCol) {
        itemIdCol.eachCell({ includeEmpty: false }, (cell, rowNum) => {
          if (rowNum > 1 && cell.value != null) existingItemIds.add(String(cell.value));
        });
      }
    } else {
      daily = workbook.addWorksheet(dailyName);
      daily.columns = COLS;
      daily.getRow(1).font = { bold: true };
      daily.views = [{ state: 'frozen', ySplit: 1 }];
    }
    const todaysCandidate = rows.filter((r) =>
      r.inserted_at instanceof Date
      && r.inserted_at.toISOString().slice(0, 10) === TODAY_UTC
    );
    const appended = todaysCandidate.filter((r) => !existingItemIds.has(String(r.item_id)));
    daily.addRows(appended);

    // Progress tab — regenerated each run, work-order intel from master_card_catalog
    const progressName = `${code} - Progress`;
    const existingProgress = workbook.getWorksheet(progressName);
    if (existingProgress) workbook.removeWorksheet(existingProgress.id);
    const progress = workbook.addWorksheet(progressName);
    progress.columns = PROGRESS_COLS;
    progress.getRow(1).font = { bold: true };
    progress.views = [{ state: 'frozen', ySplit: 1 }];

    const catalogRows = catalogBySet.get(code) || [];
    let nDone = 0, nPartial = 0, nNotStarted = 0;
    for (const c of catalogRows) {
      const status = statusForCompCount(c.comp_count);
      if      (status.label === 'Done')        nDone++;
      else if (status.label === 'Partial')     nPartial++;
      else                                     nNotStarted++;
      const addedRow = progress.addRow({
        card_number:   c.card_number,
        card_name:     c.card_name,
        edition:       c.edition,
        print_variant: c.print_variant,
        card_code:     c.card_code,
        comp_count:    c.comp_count,
        status:        status.label,
      });
      const statusCell = addedRow.getCell('status');
      statusCell.fill = status.fill;
      statusCell.font = status.font;
      statusCell.alignment = { horizontal: 'center' };
    }

    // Tally banner at the top: prepend a frozen 2-row header showing totals
    if (catalogRows.length > 0) {
      // Insert summary row above the table by shifting everything down — exceljs
      // doesn't have a clean "prepend" so we use a side-panel approach instead:
      // populate H1..K1 with totals next to the column headers.
      progress.getCell('I1').value = `Done: ${nDone}`;
      progress.getCell('I1').fill = FILL_GREEN;
      progress.getCell('I1').font = FONT_GREEN;
      progress.getCell('I1').alignment = { horizontal: 'center' };
      progress.getCell('J1').value = `Partial: ${nPartial}`;
      progress.getCell('J1').fill = FILL_YELLOW;
      progress.getCell('J1').font = FONT_YELLOW;
      progress.getCell('J1').alignment = { horizontal: 'center' };
      progress.getCell('K1').value = `Not Started: ${nNotStarted}`;
      progress.getCell('K1').fill = FILL_RED;
      progress.getCell('K1').font = FONT_RED;
      progress.getCell('K1').alignment = { horizontal: 'center' };
      progress.getColumn('I').width = 12;
      progress.getColumn('J').width = 13;
      progress.getColumn('K').width = 17;
    }

    // Summary row
    const latestSoldAt = rows.find((r) => r.sold_at)?.sold_at || null;
    let latestInsertedAt = null;
    for (const r of rows) {
      if (r.inserted_at && (!latestInsertedAt || r.inserted_at > latestInsertedAt)) {
        latestInsertedAt = r.inserted_at;
      }
    }
    const totalCatalog = catalogRows.length;
    summaryRows.push({
      set_code: code,
      set_name: rows[0]?.set_name || '',
      total_rows: rows.length,
      rows_today_appended: appended.length,
      rows_today_total: existingItemIds.size + appended.length,
      latest_sold_at: latestSoldAt,
      latest_inserted_at: latestInsertedAt,
      cards_done: nDone,
      cards_partial: nPartial,
      cards_not_started: nNotStarted,
      pct_done: totalCatalog > 0 ? nDone / totalCatalog : 0,
    });
  }

  // Generate Progress tabs for sets present in master_card_catalog but with
  // ZERO ebay_transactions rows (so they didn't iterate above). Without this
  // a brand-new set the operator hasn't scraped yet would have no Progress tab.
  for (const [code, catalogRows] of catalogBySet) {
    if (sortedSetCodes.includes(code)) continue;  // already handled above
    if (catalogRows.length === 0) continue;

    const progressName = `${code} - Progress`;
    const existingProgress = workbook.getWorksheet(progressName);
    if (existingProgress) workbook.removeWorksheet(existingProgress.id);
    const progress = workbook.addWorksheet(progressName);
    progress.columns = PROGRESS_COLS;
    progress.getRow(1).font = { bold: true };
    progress.views = [{ state: 'frozen', ySplit: 1 }];

    let nDone = 0, nPartial = 0, nNotStarted = 0;
    for (const c of catalogRows) {
      const status = statusForCompCount(c.comp_count);
      if      (status.label === 'Done')        nDone++;
      else if (status.label === 'Partial')     nPartial++;
      else                                     nNotStarted++;
      const addedRow = progress.addRow({
        card_number:   c.card_number,
        card_name:     c.card_name,
        edition:       c.edition,
        print_variant: c.print_variant,
        card_code:     c.card_code,
        comp_count:    c.comp_count,
        status:        status.label,
      });
      const statusCell = addedRow.getCell('status');
      statusCell.fill = status.fill;
      statusCell.font = status.font;
      statusCell.alignment = { horizontal: 'center' };
    }

    progress.getCell('I1').value = `Done: ${nDone}`;
    progress.getCell('I1').fill = FILL_GREEN;
    progress.getCell('I1').font = FONT_GREEN;
    progress.getCell('I1').alignment = { horizontal: 'center' };
    progress.getCell('J1').value = `Partial: ${nPartial}`;
    progress.getCell('J1').fill = FILL_YELLOW;
    progress.getCell('J1').font = FONT_YELLOW;
    progress.getCell('J1').alignment = { horizontal: 'center' };
    progress.getCell('K1').value = `Not Started: ${nNotStarted}`;
    progress.getCell('K1').fill = FILL_RED;
    progress.getCell('K1').font = FONT_RED;
    progress.getCell('K1').alignment = { horizontal: 'center' };
    progress.getColumn('I').width = 12;
    progress.getColumn('J').width = 13;
    progress.getColumn('K').width = 17;

    summaryRows.push({
      set_code: code,
      set_name: catalogRows[0]?.set_name || '',
      total_rows: 0,
      rows_today_appended: 0,
      rows_today_total: 0,
      latest_sold_at: null,
      latest_inserted_at: null,
      cards_done: nDone,
      cards_partial: nPartial,
      cards_not_started: nNotStarted,
      pct_done: catalogRows.length > 0 ? nDone / catalogRows.length : 0,
    });
  }

  // Summary tab — regenerated each run
  const existingSummary = workbook.getWorksheet('Summary');
  if (existingSummary) workbook.removeWorksheet(existingSummary.id);
  const summary = workbook.addWorksheet('Summary');
  summary.columns = SUMMARY_COLS;
  summary.addRows(summaryRows);
  summary.getRow(1).font = { bold: true };
  summary.views = [{ state: 'frozen', ySplit: 1 }];

  // Export-run info block underneath the summary table
  const infoStart = summaryRows.length + 3;
  const info = [
    ['Export run at (UTC):', new Date().toISOString()],
    ['Daily tab date (UTC):', TODAY_UTC],
    ['Filtered sets:',        SETS_FILTER ? SETS_FILTER.join(',') : '(all)'],
    ['Source DB:',            `${PG_USER}@${PG_HOST}:${PG_PORT}/${PG_DB}`],
    ['Workbook state:',       existed ? 'updated existing file' : 'created new file'],
  ];
  info.forEach((row, i) => {
    summary.getCell(`A${infoStart + i}`).value = row[0];
    summary.getCell(`A${infoStart + i}`).font  = { bold: true };
    summary.getCell(`B${infoStart + i}`).value = row[1];
  });

  // Ensure the output directory exists
  fs.mkdirSync(path.dirname(OUT_PATH), { recursive: true });
  await workbook.xlsx.writeFile(OUT_PATH);

  const totalAppended = summaryRows.reduce((s, r) => s + r.rows_today_appended, 0);
  const totalDone = summaryRows.reduce((s, r) => s + r.cards_done, 0);
  const totalPartial = summaryRows.reduce((s, r) => s + r.cards_partial, 0);
  const totalNotStarted = summaryRows.reduce((s, r) => s + r.cards_not_started, 0);
  console.log('');
  console.log('── Export report ───────────────────────────────');
  console.log(`  Output:           ${OUT_PATH}`);
  console.log(`  Workbook state:   ${existed ? 'updated existing' : 'created new'}`);
  console.log(`  Sets exported:    ${bySet.size}  (catalog Progress: ${catalogBySet.size})`);
  console.log(`  Total rows:       ${res.rows.length}`);
  console.log(`  Today's appends:  ${totalAppended}`);
  console.log(`  Cards Done:       ${totalDone}    (>= ${PROGRESS_DONE_THRESHOLD} comps)`);
  console.log(`  Cards Partial:    ${totalPartial}    (1-${PROGRESS_DONE_THRESHOLD-1} comps)`);
  console.log(`  Cards Not Started:${totalNotStarted}    (0 comps)`);
  console.log('────────────────────────────────────────────────');
  for (const s of summaryRows) {
    const total = s.cards_done + s.cards_partial + s.cards_not_started;
    const pct = total > 0 ? (s.cards_done / total * 100).toFixed(1) : '—';
    console.log(`  ${s.set_code.padEnd(4)}  ${String(s.cards_done).padStart(3)} done  ${String(s.cards_partial).padStart(3)} partial  ${String(s.cards_not_started).padStart(3)} not-started  (${pct}% complete)`);
  }
  console.log('────────────────────────────────────────────────');
}

main().catch((e) => {
  console.error('fatal:', e.message);
  if (e.code) console.error('       code:', e.code);
  process.exit(1);
});
