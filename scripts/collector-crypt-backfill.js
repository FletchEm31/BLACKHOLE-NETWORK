#!/usr/bin/env node
// collector-crypt-backfill.js — BHN
//
// Pulls all Collector Crypt graded Pokemon card sales from Magic Eden activities,
// enriches each NFT's traits via Helius DAS getAsset, converts SOL→USD via
// CoinGecko historical daily prices, inserts into collector_crypt_transactions.
//
// Pipeline:
//   Magic Eden activities (buyNow) → batch Helius DAS getAsset → filter Category=Pokemon
//   → normalize grade/grader/cert/language → CoinGecko SOL/USD rate → INSERT
//
// Idempotent: ON CONFLICT (item_id) DO NOTHING. Safe to re-run.
// State:      /var/lib/bhn-cc-backfill/state.json for kill/resume.
//
// Usage (run on LA, peer auth as postgres):
//   HELIUS_API_KEY=xxx sudo -u postgres env HTTPS_PROXY=http://<BHN_WG_HIL_IP>:8888 \
//     node scripts/collector-crypt-backfill.js [--dry-run] [--max-pages N]
//   flags: --host /var/run/postgresql  --db eventhorizon  --user postgres
//          --dry-run   (report only, no writes)
//          --max-pages N  (stop after N ME activity pages)

'use strict';

const https  = require('https');
const http   = require('http');
const fs     = require('fs');
const path   = require('path');
const { Client } = require('pg');

// Proxy agent — tinyproxy requires CONNECT tunnelling for https targets, which a
// plain absolute-URL GET does not provide. HttpsProxyAgent does it correctly.
// Handles both the v5 (default export) and v6+ ({ HttpsProxyAgent }) export shapes.
let HttpsProxyAgent;
try {
  const _m = require('https-proxy-agent');
  HttpsProxyAgent = _m.HttpsProxyAgent || _m;
} catch { /* no proxy agent installed — direct only */ }

function proxyAgent() {
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
  if (!proxy || !HttpsProxyAgent) return undefined;
  return new HttpsProxyAgent(proxy);
}

// ── Constants ─────────────────────────────────────────────────────────────────

const ME_BASE      = 'https://api-mainnet.magiceden.dev/v2';
const ME_SYMBOL    = 'collector_crypt';
const CC_COLLECTION= 'CCryptWBYktukHDQ2vHGtVcmtjXxYzvw8XNVY64YN2Yf';
const CG_BASE      = 'https://api.coingecko.com/api/v3';
const DEFAULT_STATE= '/var/lib/bhn-cc-backfill/state.json';
const HELIUS_RPC   = `https://mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY || ''}`;

const WOTC_SET_MAP = {
  'BASE': 'Base Set', 'BASE SET': 'Base Set', 'POKEMON BASE SET': 'Base Set',
  'FOSSIL': 'Fossil', 'POKEMON FOSSIL': 'Fossil',
  'JUNGLE': 'Jungle', 'POKEMON JUNGLE': 'Jungle',
  'TEAM ROCKET': 'Team Rocket', 'POKEMON TEAM ROCKET': 'Team Rocket',
  'GYM HEROES': 'Gym Heroes',   'POKEMON GYM HEROES': 'Gym Heroes',
  'GYM CHALLENGE': 'Gym Challenge', 'POKEMON GYM CHALLENGE': 'Gym Challenge',
  'WIZARDS BLACK STAR PROMOS': 'Wizards Black Star Promos',
  'BEST OF GAME': 'Best of Game',
};

// ── CLI ───────────────────────────────────────────────────────────────────────

const argv     = process.argv.slice(2);
const flag     = (k, d = null) => { const i = argv.indexOf(k); return i >= 0 && argv[i+1] ? argv[i+1] : d; };
const hasF     = k => argv.includes(k);
const DRY_RUN  = hasF('--dry-run');
const MAX_PAGES= flag('--max-pages') ? parseInt(flag('--max-pages'), 10) : null;
const PG_HOST  = flag('--host', process.env.PGHOST || '/var/run/postgresql');
const PG_DB    = flag('--db',   process.env.PGDATABASE || 'eventhorizon');
const PG_USER  = flag('--user', process.env.PGUSER || 'postgres');
const STATE_FILE = flag('--state', DEFAULT_STATE);

if (!process.env.HELIUS_API_KEY) {
  console.error('ERROR: HELIUS_API_KEY env var required'); process.exit(2);
}

// ── HTTP helper (respects HTTPS_PROXY) ────────────────────────────────────────

function httpGet(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const opts = {
      hostname: u.hostname,
      path: u.pathname + u.search,
      // CoinGecko 403s requests without a descriptive User-Agent; harmless elsewhere.
      headers: { 'User-Agent': 'BHN-collector/1.0', ...headers },
      agent: proxyAgent(),   // CONNECT tunnel through tinyproxy when HTTPS_PROXY set
    };

    const req = https.get(opts, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if (res.statusCode === 429) { reject(new Error('RATE_LIMIT')); return; }
        if (res.statusCode >= 400) { reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0,200)}`)); return; }
        try { resolve(JSON.parse(body)); } catch { resolve(body); }
      });
    });
    req.on('error', reject);
    req.setTimeout(30000, () => { req.destroy(new Error('timeout')); });
  });
}

function httpPost(url, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const payload = JSON.stringify(body);
    const opts = {
      hostname: u.hostname,
      path: u.pathname + u.search,
      method: 'POST',
      headers: { 'User-Agent': 'BHN-collector/1.0', 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      agent: proxyAgent(),   // CONNECT tunnel through tinyproxy when HTTPS_PROXY set
    };

    const req = https.request(opts, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => {
        const b = Buffer.concat(chunks).toString('utf8');
        if (res.statusCode >= 400) { reject(new Error(`HTTP ${res.statusCode}: ${b.slice(0,200)}`)); return; }
        try { resolve(JSON.parse(b)); } catch { resolve(b); }
      });
    });
    req.on('error', reject);
    req.setTimeout(30000, () => { req.destroy(new Error('timeout')); });
    req.write(payload);
    req.end();
  });
}

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── State ─────────────────────────────────────────────────────────────────────

function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
  } catch {}
  return { offset: 0, inserted: 0, skipped_non_pokemon: 0, skipped_dup: 0, errors: 0, started_at: new Date().toISOString() };
}

function saveState(s) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}

// ── Magic Eden ────────────────────────────────────────────────────────────────

async function fetchActivities(offset, limit = 500) {
  const url = `${ME_BASE}/collections/${ME_SYMBOL}/activities?offset=${offset}&limit=${limit}&type=buyNow`;
  const data = await httpGet(url);
  return Array.isArray(data) ? data : [];
}

// ── Helius DAS ────────────────────────────────────────────────────────────────

async function getAssetBatch(mints) {
  if (!mints.length) return [];
  const resp = await httpPost(HELIUS_RPC, {
    jsonrpc: '2.0', id: 'cc-batch', method: 'getAssetBatch',
    params: { ids: mints },
  });
  return Array.isArray(resp.result) ? resp.result : [];
}

function findAttr(attrs, ...names) {
  if (!Array.isArray(attrs)) return null;
  for (const name of names) {
    const t = attrs.find(a => (a.trait_type || '').toLowerCase() === name.toLowerCase());
    if (t && t.value != null && String(t.value).trim()) return String(t.value).trim();
  }
  return null;
}

// ── Grade normalisation (per locked strategy) ─────────────────────────────────

function normalizeGrade(grader, ccLabel, gradeNum) {
  const n = String(gradeNum || '').trim();
  const label = String(ccLabel || '').trim().toUpperCase();
  if (!grader || !n) return { grade: null, grade_label: null };

  if (grader === 'PSA' || grader === 'TAG') {
    return { grade: n, grade_label: null };
  }

  const num = parseFloat(n);

  if (grader === 'CGC') {
    if (num === 10) {
      if (label.includes('PRISTINE'))               return { grade: 'Pristine 10', grade_label: 'Pristine' };
      if (label.includes('PERFECT'))                return { grade: 'Perfect 10',  grade_label: 'Perfect'  };
      if (label.includes('GEM MINT') || label.includes('GEM-MINT'))
                                                     return { grade: 'Gem Mint 10', grade_label: 'Gem Mint' };
      return { grade: null, grade_label: null }; // ambiguous — reject
    }
    if (num === 9.5) {
      if (label.includes('GEM MINT') || label.includes('GEM-MINT'))
                                                     return { grade: 'Gem Mint 9.5', grade_label: 'Gem Mint' };
      return { grade: 'Mint+ 9.5', grade_label: 'Mint+' }; // current black label default
    }
    return { grade: n, grade_label: null }; // 9 and below: bare numeric
  }

  if (grader === 'BGS') {
    if (num === 10) return { grade: '10', grade_label: null }; // can't distinguish 3 BGS 10s
    return { grade: n, grade_label: null };
  }

  if (grader === 'SGC') {
    if (num === 10) {
      if (label.includes('PRISTINE'))               return { grade: 'Pristine 10', grade_label: 'Pristine' };
      // SGC 10 default is Gem Mint 10
                                                     return { grade: 'Gem Mint 10', grade_label: 'Gem Mint' };
    }
    return { grade: n, grade_label: null };
  }

  return { grade: null, grade_label: null };
}

function normalizeGrader(raw) {
  if (!raw) return null;
  const u = raw.toUpperCase();
  if (u.includes('PSA')) return 'PSA';
  if (u.includes('CGC')) return 'CGC';
  if (u.includes('BGS') || u.includes('BECKETT')) return 'BGS';
  if (u.includes('SGC')) return 'SGC';
  if (u.includes('TAG')) return 'TAG';
  return null;
}

function normalizeCardNumber(raw) {
  if (!raw) return null;
  return String(raw).replace(/^#/, '').replace(/\/\d+$/, '').trim() || null;
}

// ── CoinGecko SOL/USD daily rate (cached) ─────────────────────────────────────

const solRateCache = {};
const CG_KEY = process.env.COINGECKO_API_KEY || '';

async function getSolUsdRate(blockTime) {
  const d = new Date(blockTime * 1000);
  const key = `${String(d.getUTCDate()).padStart(2,'0')}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${d.getUTCFullYear()}`;
  if (solRateCache[key]) return solRateCache[key];
  const url = `${CG_BASE}/coins/solana/history?date=${key}&localization=false`;
  // Only send the demo-key header when a REAL key is set. An empty header value
  // makes CoinGecko's gateway reject the request as an invalid demo key.
  const headers = CG_KEY ? { 'x-cg-demo-api-key': CG_KEY } : {};
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const data = await httpGet(url, headers);
      const rate = data?.market_data?.current_price?.usd || null;
      if (rate) solRateCache[key] = rate;
      await sleep(CG_KEY ? 500 : 2500); // public tier is heavily throttled
      return rate;
    } catch (e) {
      if (String(e && e.message).includes('RATE_LIMIT')) { await sleep(8000); continue; }
      return null;
    }
  }
  return null;
}

// ── Build row ─────────────────────────────────────────────────────────────────

async function buildRow(activity, asset) {
  const attrs = asset?.content?.metadata?.attributes || [];

  const graderRaw  = findAttr(attrs, 'Grading Company', 'Grader');
  const grader     = normalizeGrader(graderRaw);
  const gradeNum   = findAttr(attrs, 'GradeNum', 'Grade Num');
  const ccLabel    = findAttr(attrs, 'The Grade', 'Grade');
  const { grade, grade_label } = normalizeGrade(grader, ccLabel, gradeNum);

  const ccSetRaw   = findAttr(attrs, 'Set', 'Set Name');
  const setName    = ccSetRaw ? (WOTC_SET_MAP[ccSetRaw.toUpperCase()] || null) : null;
  const language   = findAttr(attrs, 'Language');
  const certNumber = findAttr(attrs, 'Grading ID', 'Serial', 'Cert Number');
  const cardName   = findAttr(attrs, 'Title/Subject', 'Card Name', 'Name');
  const cardNumRaw = findAttr(attrs, 'Serial Number', 'Card Number', '#');
  const cardNumber = normalizeCardNumber(cardNumRaw);

  const solPrice   = activity.price || null;
  const solRate    = solPrice ? await getSolUsdRate(activity.blockTime) : null;
  const soldPrice  = (solPrice && solRate) ? Math.round(solPrice * solRate * 100) / 100 : null;

  return {
    item_id:          activity.signature,
    title:            asset?.content?.metadata?.name || null,
    card_name:        cardName,
    grader,
    grade,
    grade_label,
    cert_number:      certNumber,
    set_name:         setName,
    cc_set_name:      ccSetRaw || null,
    language,
    card_number:      cardNumber,
    edition:          'N/A',
    print_variant:    'Standard',
    sold_price:       soldPrice,
    sol_price:        solPrice,
    sol_usd_rate:     solRate,
    currency:         'USDC',
    platform:         'collector_crypt',
    blockchain:       'solana',
    transaction_hash: activity.signature,
    sale_type:        'peer_to_peer',
    seller_address:   activity.seller || null,
    buyer_address:    activity.buyer  || null,
    seller_username:  activity.seller || null,
    nft_contract:     activity.tokenMint || null,
    listing_url:      activity.tokenMint
                        ? `https://magiceden.io/item-details/${activity.tokenMint}`
                        : null,
    image_url:        asset?.content?.files?.[0]?.uri || null,
    item_creation_date: activity.blockTime ? new Date(activity.blockTime * 1000).toISOString() : null,
    created_at:       activity.blockTime ? new Date(activity.blockTime * 1000).toISOString() : null,
    raw_payload:      JSON.stringify({ me_activity: activity, helius_asset: asset }),
  };
}

// ── INSERT ────────────────────────────────────────────────────────────────────

const INSERT_SQL = `
INSERT INTO collector_crypt_transactions (
  item_id, title, card_name, grader, grade, grade_label, cert_number,
  set_name, cc_set_name, language, card_number, edition, print_variant,
  sold_price, sol_price, sol_usd_rate, currency,
  platform, blockchain, transaction_hash, sale_type,
  seller_address, buyer_address, seller_username,
  nft_contract, listing_url, image_url, item_creation_date, created_at,
  raw_payload
) VALUES (
  $1,$2,$3,$4,$5,$6,$7,
  $8,$9,$10,$11,$12,$13,
  $14,$15,$16,$17,
  $18,$19,$20,$21,
  $22,$23,$24,
  $25,$26,$27,$28,$29,
  $30::jsonb
)
ON CONFLICT (item_id) DO NOTHING`;

// ── Main ──────────────────────────────────────────────────────────────────────

(async () => {
  const state = loadState();
  console.log(`[cc-backfill] resume from offset ${state.offset} | prior: ${state.inserted} inserted`);

  const client = DRY_RUN ? null : new Client({
    host: PG_HOST, database: PG_DB, user: PG_USER, port: 5432,
  });
  if (client) await client.connect();

  let pages = 0;

  try {
    while (true) {
      if (MAX_PAGES !== null && pages >= MAX_PAGES) break;

      const activities = await fetchActivities(state.offset);
      if (!activities.length) { console.log('[cc-backfill] no more activities — done.'); break; }

      // Batch Helius DAS enrichment (up to 100 mints per call)
      const mints   = [...new Set(activities.map(a => a.tokenMint).filter(Boolean))];
      const assets  = await getAssetBatch(mints.slice(0, 100));
      if (mints.length > 100) {
        // Process remainder in follow-up batch(es)
        for (let i = 100; i < mints.length; i += 100) {
          const more = await getAssetBatch(mints.slice(i, i + 100));
          assets.push(...more);
          await sleep(300);
        }
      }
      const assetMap = Object.fromEntries(assets.filter(Boolean).map(a => [a.id, a]));

      for (const activity of activities) {
        const asset  = assetMap[activity.tokenMint];
        const attrs  = asset?.content?.metadata?.attributes || [];
        const cat    = attrs.find(a => (a.trait_type || '').toLowerCase() === 'category');

        // Filter: Pokemon only
        if (!cat || String(cat.value).toUpperCase() !== 'POKEMON') {
          state.skipped_non_pokemon++;
          continue;
        }

        const row = await buildRow(activity, asset);

        if (DRY_RUN) {
          console.log('[dry-run]', JSON.stringify({ item_id: row.item_id, card_name: row.card_name, grader: row.grader, grade: row.grade, sold_price: row.sold_price }));
          state.inserted++;
          continue;
        }

        try {
          const vals = [
            row.item_id, row.title, row.card_name, row.grader, row.grade, row.grade_label, row.cert_number,
            row.set_name, row.cc_set_name, row.language, row.card_number, row.edition, row.print_variant,
            row.sold_price, row.sol_price, row.sol_usd_rate, row.currency,
            row.platform, row.blockchain, row.transaction_hash, row.sale_type,
            row.seller_address, row.buyer_address, row.seller_username,
            row.nft_contract, row.listing_url, row.image_url, row.item_creation_date, row.created_at,
            row.raw_payload,
          ];
          const res = await client.query(INSERT_SQL, vals);
          if (res.rowCount > 0) state.inserted++;
          else                   state.skipped_dup++;
        } catch (e) {
          console.error('[error]', activity.signature, e.message);
          state.errors++;
        }
      }

      state.offset += activities.length;
      pages++;
      saveState(state);
      console.log(`[cc-backfill] page ${pages} done — offset ${state.offset} | pokemon: +${state.inserted} inserted | non-pokemon skipped: ${state.skipped_non_pokemon}`);

      if (activities.length < 500) { console.log('[cc-backfill] last page reached.'); break; }
      await sleep(500); // ME rate-limit cushion
    }
  } finally {
    if (client) await client.end();
  }

  console.log('\n── CC backfill report ──────────────────────────');
  console.log('Inserted:           ', state.inserted);
  console.log('Skipped (dup):      ', state.skipped_dup);
  console.log('Skipped (non-pokemon):', state.skipped_non_pokemon);
  console.log('Errors:             ', state.errors);
  console.log('Final offset:       ', state.offset);
  saveState(state);
})();
