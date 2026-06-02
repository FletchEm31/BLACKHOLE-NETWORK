#!/usr/bin/env node
// BHN eBay sold-comps multi-set driver with blessed-contrib terminal dashboard.
//
// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  DEPLOYMENT RULES — NON-NEGOTIABLE                                          ║
// ║  • This scraper MUST only run on LA (10.8.0.1).                             ║
// ║  • NEVER run from the operator's home IP.                                   ║
// ║  • NEVER authenticate to eBay from LA. Guest-only, no cookies, no login.   ║
// ║  • Set  BHN_RUN_ON_LA=1  in the LA environment to unlock execution.        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝
//
// Reads active cards from master_card_catalog (or --sets filter), scrapes in order:
//   BOG → WSP → FSL → JGL → TRK → BST → GYH → GYC
//
// TRK supplement mode: queries existing ebay_transactions item_ids first to skip dupes.
//
// Usage:
//   node ebay-sold-scrape-all.js [options]
//
// Options:
//   --sets TRK,BST           Only run these set codes (comma-separated)
//   --checkpoint-dir <path>  Where to store/read checkpoint files (default: ./checkpoints)
//   --config <path>          Config file path (default: ./ebay_scraper_config.json)
//   --no-ui                  Headless mode (for SSH sessions without terminal)
//   --dry-run                Parse + plan without writing to DB
//   --host / --db / --user   Postgres connection flags
//   --force-local            Bypass LA guard for offline testing (no real requests)

'use strict';

// ── Deployment guard ───────────────────────────────────────────────────────────
(function assertRunningOnLA() {
  const isApproved = process.env.BHN_RUN_ON_LA === '1';
  const forceLocal = process.argv.includes('--force-local');
  if (!isApproved && !forceLocal) {
    console.error(
      '\n╔══════════════════════════════════════════════════════════════════════════════╗\n' +
      '║  BLOCKED: BHN_RUN_ON_LA=1 is not set.                                      ║\n' +
      '║  This scraper must only run on the LA server (10.8.0.1).                   ║\n' +
      '║  Running it from your home IP risks associating your personal IP with       ║\n' +
      '║  automated eBay scraping. Deploy to LA and set BHN_RUN_ON_LA=1 there.      ║\n' +
      '╚══════════════════════════════════════════════════════════════════════════════╝\n'
    );
    process.exit(1);
  }
  if (forceLocal && !isApproved) {
    console.warn('[WARN] --force-local set: running outside LA. No real HTTP requests should be made.');
  }
})();

// ── Egress: LA-direct, no proxy ──────────────────────────────────────────────
// The old FRA SOCKS5 tunnel (Frankfurt 10.9.0.2 / 192.248.187.208) was RETIRED
// 2026-05-28 when Frankfurt was decommissioned — 10.9.0.2 is unreachable. It is
// also no longer needed: the 2026-05-28 TLS-fingerprint finding showed the eBay
// block is TLS/JA3-fingerprint-based, not IP reputation, and impers+firefox144
// returns real listings directly from LA's own IP (see fetchPage in
// ebay-sold-scrape.js, and project_ebay_tls_fingerprint_impers_2026-05-28).
// The scraper now egresses LA-direct via impers — no SOCKS, no BHN_SOCKS_PROXY.

const fs   = require('fs');
const path = require('path');
const { Client } = require('pg');

const {
  scrapeSet, buildPbdsCode, hotReloadConfig, pickSessionUA,
} = require('./ebay-sold-scrape');

// ── CLI flags ──────────────────────────────────────────────────────────────────
const args   = process.argv.slice(2);
const flag   = (k) => { const i = args.indexOf(k); return i >= 0 && args[i+1] ? args[i+1] : null; };
const hasF   = (k) => args.includes(k);

const SETS_FILTER    = flag('--sets')           ? flag('--sets').split(',').map(s=>s.trim().toUpperCase()) : null;
const EDITION_FILTER = flag('--edition')        ? flag('--edition').split(',').map(s=>s.trim()) : null;
const CHECKPOINT_DIR = flag('--checkpoint-dir') || path.join(__dirname, 'checkpoints');
const CONFIG_PATH    = flag('--config')         || path.join(__dirname, 'ebay_scraper_config.json');
const NO_UI          = hasF('--no-ui');
const DRY_RUN        = hasF('--dry-run');

const PG_HOST = flag('--host') || process.env.PGHOST     || '10.8.0.1';
const PG_DB   = flag('--db')   || process.env.PGDATABASE || 'eventhorizon';
const PG_USER = flag('--user') || process.env.PGUSER     || 'postgres';
const PG_PORT = parseInt(flag('--port') || process.env.PGPORT || '5432', 10);

// Set run order per spec: BOG → WSP → FSL → JGL → TRK → BST → GYH → GYC
const SET_ORDER = ['BOG','WSP','FSL','JGL','TRK','BST','GYH','GYC'];

const SET_CODE_TO_NAME = {
  BST: 'Base Set', FSL: 'Fossil', JGL: 'Jungle', TRK: 'Team Rocket',
  GYH: 'Gym Heroes', GYC: 'Gym Challenge', WSP: 'Wizards Black Star Promos', BOG: 'Best of Game',
};

// ── State (shared between UI and scraper via callbacks) ────────────────────────
const state = {
  paused:          false,
  skipCurrent:     false,
  delayMultiplier: 1.0,
  shutdown:        false,

  // Stats
  rowsCaptured:    0,
  rowsSkipped:     0,
  rowsRejected:    0,
  zeroResults:     0,
  requests:        0,
  rateLimitHits:   0,

  // Progress
  currentSet:      '',
  cardIndex:       0,
  totalCards:      0,
  startTime:       Date.now(),
};

const logBuffer  = [];   // all log lines
const rateBuffer = [];   // rate-limit events only
let   reqTimes   = [];   // rolling window for req/min calculation

// ── Logging (works headless and with UI) ──────────────────────────────────────
function addLog(msg, level) {
  const ts   = new Date().toISOString().slice(11, 19);
  const line = `[${ts}] ${msg}`;
  logBuffer.push({ line, level });
  if (level === 'warn' || level === 'error') rateBuffer.push({ line, level });
  if (NO_UI) {
    const prefix = level === 'error' ? '✗' : level === 'warn' ? '⚠' : '·';
    console.log(`${prefix} ${line}`);
  } else if (uiRefs.log) {
    try { uiRefs.log.log(line); uiRefs.screen.render(); } catch {}
  }
}

// ── Elapsed / ETA helpers ─────────────────────────────────────────────────────
function fmtDuration(ms) {
  const s   = Math.floor(ms / 1000);
  const h   = Math.floor(s / 3600);
  const m   = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
}

function calcEta() {
  const elapsed = Date.now() - state.startTime;
  if (state.cardIndex === 0) return '--:--:--';
  const msPerCard = elapsed / state.cardIndex;
  const remaining = (state.totalCards - state.cardIndex) * msPerCard;
  return fmtDuration(remaining);
}

function calcRpm() {
  const now = Date.now();
  reqTimes = reqTimes.filter(t => now - t < 60000);
  return reqTimes.length;
}

// ── UI refs (populated by initUI) ────────────────────────────────────────────
const uiRefs = {};

function initUI() {
  if (NO_UI) return;
  try {
    const blessed      = require('blessed');
    const contrib      = require('blessed-contrib');

    const screen = blessed.screen({ smartCSR: true, title: 'BHN eBay Sold Comps Scraper' });
    uiRefs.screen = screen;

    const grid = new contrib.grid({ rows: 12, cols: 12, screen });

    // Header box (full-width, 2 rows)
    const header = grid.set(0, 0, 2, 12, blessed.box, {
      label: ' BHN eBay Scraper ',
      tags: true, border: { type: 'line' }, style: { border: { fg: 'cyan' } },
    });
    uiRefs.header = header;

    // Live feed log (left 8 cols, rows 2–9)
    const log = grid.set(2, 0, 7, 8, contrib.log, {
      label: ' Live Feed ', fg: 'green', selectedFg: 'green',
      border: { type: 'line' }, style: { border: { fg: 'green' } },
    });
    uiRefs.log = log;

    // Stats table (right 4 cols, rows 2–6)
    const statsTable = grid.set(2, 8, 5, 4, contrib.table, {
      label: ' Stats ',
      keys: false, fg: 'white', selectedFg: 'white', selectedBg: 'blue',
      interactive: false,
      border: { type: 'line' }, style: { border: { fg: 'yellow' } },
      columnSpacing: 2,
      columnWidth: [14, 6],
    });
    uiRefs.statsTable = statsTable;

    // Config / rate-limit panel (right 4 cols, rows 7–9)
    const infoBox = grid.set(7, 8, 2, 4, blessed.box, {
      label: ' Config [C] · Rate [R] ',
      tags: true, border: { type: 'line' }, style: { border: { fg: 'blue' } },
      content: 'Press C or R',
    });
    uiRefs.infoBox = infoBox;

    // Controls bar (full-width, rows 9–10)
    const controls = grid.set(9, 0, 1, 12, blessed.box, {
      content: '{bold}P{/bold}:Pause/Resume  {bold}S{/bold}:Skip  {bold}+/-{/bold}:Delay  {bold}Q{/bold}:Quit  {bold}R{/bold}:RateLog  {bold}C{/bold}:Config',
      tags: true, border: { type: 'line' },
      style: { border: { fg: 'gray' }, fg: 'white' },
    });
    uiRefs.controls = controls;

    // Keyboard handlers
    screen.key('p', () => {
      state.paused = !state.paused;
      addLog(`[${state.paused ? 'PAUSED' : 'RESUMED'}] Operator ${state.paused ? 'paused' : 'resumed'} scraper`, 'info');
      updateHeader();
    });

    screen.key('s', () => {
      state.skipCurrent = true;
      addLog('[SKIP] Skipping current card…', 'info');
    });

    screen.key('+', () => {
      state.delayMultiplier = Math.min(+(state.delayMultiplier + 0.5).toFixed(1), 5.0);
      addLog(`[CONFIG] Delay multiplier → ${state.delayMultiplier}x`, 'info');
      updateStats();
    });

    screen.key('-', () => {
      state.delayMultiplier = Math.max(+(state.delayMultiplier - 0.5).toFixed(1), 0.5);
      addLog(`[CONFIG] Delay multiplier → ${state.delayMultiplier}x`, 'info');
      updateStats();
    });

    screen.key('q', () => gracefulShutdown());

    screen.key('r', () => {
      const lines = rateBuffer.slice(-10).map(e => e.line).join('\n') || '(no rate limit events)';
      uiRefs.infoBox.setLabel(' Rate Limit Log ');
      uiRefs.infoBox.setContent(lines);
      screen.render();
    });

    screen.key('c', () => {
      const cfg = hotReloadConfig(CONFIG_PATH) || {};
      const txt = Object.entries(cfg).map(([k,v]) => `${k}: ${v}`).join('\n');
      uiRefs.infoBox.setLabel(' Current Config ');
      uiRefs.infoBox.setContent(txt);
      screen.render();
    });

    screen.key(['escape','C-c'], () => gracefulShutdown());

    screen.render();
  } catch (e) {
    console.error('UI init failed (missing blessed-contrib?), falling back to headless:', e.message);
    Object.keys(uiRefs).forEach(k => delete uiRefs[k]);
  }
}

function updateHeader() {
  if (!uiRefs.header) return;
  const elapsed = fmtDuration(Date.now() - state.startTime);
  const status  = state.paused ? '{yellow-fg}PAUSED{/yellow-fg}' :
                  state.shutdown ? '{red-fg}STOPPING{/red-fg}' :
                  '{green-fg}SCRAPING{/green-fg}';
  uiRefs.header.setContent(
    `Set: {bold}${state.currentSet || '—'}{/bold}  ` +
    `Cards: {bold}${state.cardIndex}/${state.totalCards}{/bold}  ` +
    `Elapsed: ${elapsed}  ETA: ${calcEta()}  ` +
    `Status: ${status}  Delay: ${state.delayMultiplier}x`
  );
  uiRefs.screen && uiRefs.screen.render();
}

function updateStats() {
  if (!uiRefs.statsTable) return;
  uiRefs.statsTable.setData({
    headers: ['Metric', 'Value'],
    data: [
      ['Captured',    String(state.rowsCaptured)],
      ['Skipped',     String(state.rowsSkipped)],
      ['Rejected',    String(state.rowsRejected)],
      ['Zero results',String(state.zeroResults)],
      ['Requests',    String(state.requests)],
      ['Req/min',     String(calcRpm())],
      ['Rate limits', String(state.rateLimitHits)],
      ['Delay mult',  `${state.delayMultiplier}x`],
    ],
  });
  uiRefs.screen && uiRefs.screen.render();
}

// ── Graceful shutdown ─────────────────────────────────────────────────────────
let shutdownResolve;
const shutdownPromise = new Promise(r => { shutdownResolve = r; });

async function gracefulShutdown() {
  if (state.shutdown) return;
  state.shutdown = true;
  state.paused   = false;  // unblock any pause loop so scraper can exit
  addLog('[QUIT] Graceful shutdown — finishing current request…', 'warn');
  updateHeader();
  setTimeout(() => {
    if (uiRefs.screen) {
      try { uiRefs.screen.destroy(); } catch {}
    }
    shutdownResolve();
  }, 3000);
}

// ── DB helpers ────────────────────────────────────────────────────────────────
async function ensureRejectLog(client) {
  await client.query(`
    CREATE TABLE IF NOT EXISTS grade_reject_log (
      grader TEXT, raw_label TEXT, item_id TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
}

async function detectSellerCol(client) {
  const r = await client.query(`
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'ebay_transactions' AND column_name IN ('seller','seller_username')
  `);
  return r.rows.find(c => c.column_name === 'seller_username') ? 'seller_username' : 'seller';
}

async function detectGradeLabelCol(client) {
  const r = await client.query(`
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'ebay_transactions' AND column_name = 'grade_label'
  `);
  return r.rows.length > 0;
}

// Load valid (grader, raw_label) pairs from master_grade_catalog.
async function loadValidGrades(client) {
  const r = await client.query('SELECT grader, raw_label FROM master_grade_catalog');
  return new Set(r.rows.map(row => `${row.grader}|${row.raw_label}`));
}

// Insert one result row; FK violations → grade_reject_log.
async function insertRow(client, row, sellerCol, hasGradeLabel, validGrades) {
  const grader = row.grader || null;
  const grade  = row.grade  || null;
  const key    = grader && grade ? `${grader}|${grade}` : null;

  if (key && !validGrades.has(key)) {
    await client.query(
      'INSERT INTO grade_reject_log (grader, raw_label, item_id) VALUES ($1,$2,$3)',
      [grader, grade, row.item_id]
    );
    state.rowsRejected++;
    return 'rejected';
  }

  // Map row keys (legacy CSV-shape) to live v2 ebay_transactions columns.
  // Renames: pbdd_code→card_code, title→title_raw, transaction_type→sale_type, created_at→sold_at.
  // Dropped (live on ebay_asks per v2 §12) preserved in raw_payload.
  const saleTypeRaw = row.transaction_type;
  let saleType = null;
  if (saleTypeRaw) {
    const s = String(saleTypeRaw).trim().toLowerCase();
    if (s === 'auction') saleType = 'auction';
    else if (s === 'bin' || s === 'buy it now' || s === 'fixed_price' || s === 'fixed price') saleType = 'fixed_price';
    else if (s === 'best offer' || s === 'offer_accepted') saleType = 'offer_accepted';
    else if (s === 'buyback') saleType = 'buyback';
    else if (s === 'peer_to_peer' || s === 'peer to peer') saleType = 'peer_to_peer';
  }

  const rawPayload = {
    source: 'ebay-sold-scrape-all.js',
    loaded_at: new Date().toISOString(),
    listing_url: row.listing_url || null,
    condition: row.condition || null,
    returns_accepted: row.returns_accepted,
    current_bid: row.current_bid,
    seller_feedback: row.seller_feedback,
  };

  const cols = [
    'card_code','item_id','title_raw','card_name','set_name','card_number','edition',
    'print_variant','grader','grade','sold_price','currency','shipping',
    'sale_type','bid_count','sold_at', sellerCol,
    'seller_feedback_pct','cert_number','location',
    'obo_min_price','watchers','raw_payload',
  ];
  const vals = [
    row.pbdd_code, row.item_id, row.title, row.card_name, row.set_name,
    row.card_number, row.edition, row.print_variant, grader, grade,
    row.sold_price, row.currency, row.shipping,
    saleType, row.bid_count, row.created_at,
    row.seller, row.seller_feedback_pct,
    null,  // cert_number — always NULL (Option A)
    row.location,
    row.obo_min_price, row.watchers, JSON.stringify(rawPayload),
  ];

  if (hasGradeLabel) {
    cols.push('grade_label');
    vals.push(row.grade_label || null);
  }

  const ph  = vals.map((_,i) => `$${i+1}`).join(',');
  const sql = `INSERT INTO ebay_transactions (${cols.join(',')}) VALUES (${ph}) ON CONFLICT (item_id) DO NOTHING`;

  const res = await client.query(sql, vals);
  return res.rowCount > 0 ? 'inserted' : 'skipped';
}

// ── Main ───────────────────────────────────────────────────────────────────────
async function main() {
  // Connect to DB
  const client = new Client({
    host: PG_HOST, database: PG_DB, user: PG_USER,
    password: process.env.PGPASSWORD, port: PG_PORT,
  });

  if (!DRY_RUN) {
    await client.connect();
    addLog(`Connected to ${PG_HOST}/${PG_DB}`, 'info');
    await ensureRejectLog(client);
  }

  // Load grade catalog and schema info from DB
  let validGrades = new Set();
  let sellerCol   = 'seller';
  let hasGradeLabel = false;
  if (!DRY_RUN) {
    validGrades   = await loadValidGrades(client);
    sellerCol     = await detectSellerCol(client);
    hasGradeLabel = await detectGradeLabelCol(client);
    addLog(`Grade catalog: ${validGrades.size} entries loaded`, 'info');
  }

  // Load config
  const config = hotReloadConfig(CONFIG_PATH) || {
    initial_warmup_min_sec: 120, initial_warmup_max_sec: 180,
    delay_min_sec: 8, delay_max_sec: 15,
    long_pause_every_n: 12, long_pause_min_sec: 25, long_pause_max_sec: 45,
    break_every_n: 50, break_min_min: 3, break_max_min: 5,
    max_pages_per_card: 3, checkpoint_every_n_cards: 10,
  };

  // Query active cards from master_card_catalog
  let cards = [];
  if (!DRY_RUN) {
    const res = await client.query(
      `SELECT card_name, card_number, set_name, edition, print_variant
       FROM master_card_catalog
       WHERE active = true
       ${EDITION_FILTER ? 'AND edition = ANY($1)' : ''}
       ORDER BY set_name, card_number::int`,
      EDITION_FILTER ? [EDITION_FILTER] : []
    );
    cards = res.rows;
  }

  if (cards.length === 0 && !DRY_RUN) {
    console.error('No active cards found in master_card_catalog. Exiting.');
    await client.end();
    return;
  }
  if (DRY_RUN) {
    cards = [
      { card_name: 'Dark Weezing', card_number: '14', set_name: 'Team Rocket', edition: '1st Edition', print_variant: 'Holo' },
      { card_name: 'Dark Charizard', card_number: '4', set_name: 'Team Rocket', edition: '1st Edition', print_variant: 'Holo' },
    ];
  }

  // Group cards by set and apply run order
  const bySetName = {};
  for (const c of cards) {
    if (!bySetName[c.set_name]) bySetName[c.set_name] = [];
    bySetName[c.set_name].push(c);
  }

  // Build ordered list of (setCode, cards[]) to run
  const orderedSets = [];
  for (const code of SET_ORDER) {
    if (SETS_FILTER && !SETS_FILTER.includes(code)) continue;
    const setName = SET_CODE_TO_NAME[code];
    if (setName && bySetName[setName]) {
      orderedSets.push({ code, setName, cards: bySetName[setName] });
    }
  }

  state.totalCards = orderedSets.reduce((s, { cards: c }) => s + c.length, 0);

  // For TRK supplement mode: load existing item_ids so we don't re-scrape dupes
  const existingBySet = {};
  if (!DRY_RUN) {
    for (const { code, setName } of orderedSets) {
      const r = await client.query(
        'SELECT item_id FROM ebay_transactions WHERE set_name = $1', [setName]
      );
      existingBySet[code] = new Set(r.rows.map(row => row.item_id));
      if (existingBySet[code].size > 0) {
        addLog(`[${code}] Supplement mode: ${existingBySet[code].size} existing item_ids loaded`, 'info');
      }
    }
  }

  // Init blessed-contrib UI
  initUI();
  updateHeader();
  updateStats();

  // Shared reqState for delay multiplier (passed by reference into scrapeSet via callbacks).
  // warmupDone starts false so scrapeCard's cold-start pause (120-180s) fires before the
  // first eBay request — see feedback_ebay_scraper_never_auto_kickoff.
  const reqState = { delayMultiplier: state.delayMultiplier, warmupDone: false };

  // Kick off stats update ticker
  const statsTicker = NO_UI ? null : setInterval(() => {
    reqState.delayMultiplier = state.delayMultiplier;
    updateHeader();
    updateStats();
  }, 2000);

  // Main scrape loop
  for (const { code, setName, cards: setCards } of orderedSets) {
    if (state.shutdown) break;

    state.currentSet = setName;
    addLog(`\n[SET] Starting ${setName} (${code}) — ${setCards.length} cards`, 'info');

    const callbacks = {
      onLog:    (msg, level) => addLog(msg, level),

      onStats: (delta) => {
        if (delta.requests)      { state.requests      += delta.requests;      reqTimes.push(Date.now()); }
        if (delta.rowsCaptured)  state.rowsCaptured  += delta.rowsCaptured;
        if (delta.rowsSkipped)   state.rowsSkipped   += delta.rowsSkipped;
        if (delta.zeroResults)   state.zeroResults   += delta.zeroResults;
        if (delta.rateLimitHits) state.rateLimitHits += delta.rateLimitHits;
        updateStats();
      },

      onResult: async (row) => {
        if (DRY_RUN) {
          addLog(`  [DRY] ${row.item_id} | ${row.grader||'RAW'} ${row.grade||''} | $${row.sold_price}`, 'debug');
          state.rowsCaptured++;
          return;
        }
        try {
          const outcome = await insertRow(client, row, sellerCol, hasGradeLabel, validGrades);
          if (outcome === 'inserted') {
            state.rowsCaptured++;
          } else if (outcome === 'skipped') {
            state.rowsSkipped++;
          }
          // 'rejected' count is updated inside insertRow
        } catch (e) {
          addLog(`[ERROR] insert failed for ${row.item_id}: ${e.message}`, 'error');
        }
        state.cardIndex++;
        updateStats();
      },

      onCheckpoint: (s) => {
        addLog(`[CHECKPOINT] Saved at ${s.cardKey} — ${s.cardsDone} cards done, ${state.rowsCaptured} rows captured`, 'info');
      },

      isPaused:   () => state.paused,
      shouldSkip: () => {
        if (state.skipCurrent) { state.skipCurrent = false; return true; }
        return state.shutdown;
      },

      configPath: CONFIG_PATH,
      reqState,
    };

    const seenItemIds = existingBySet[code] || new Set();

    await scrapeSet(setCards, config, callbacks, CHECKPOINT_DIR, seenItemIds);

    if (!state.shutdown) {
      addLog(`[SET] ${setName} complete — ${state.rowsCaptured} total rows captured`, 'info');
    }
  }

  if (statsTicker) clearInterval(statsTicker);

  addLog('\n[DONE] All sets complete.', 'info');
  addLog(`Final: ${state.rowsCaptured} inserted · ${state.rowsSkipped} skipped · ${state.rowsRejected} rejected`, 'info');

  if (!DRY_RUN) await client.end();

  if (!NO_UI) {
    // Wait a moment so the operator can see the final state before shutdown
    addLog('Press Q to exit.', 'info');
    updateHeader();
    updateStats();
    await shutdownPromise;
  }
}

main().catch((e) => {
  if (uiRefs.screen) { try { uiRefs.screen.destroy(); } catch {} }
  console.error('fatal:', e);
  process.exit(1);
});
