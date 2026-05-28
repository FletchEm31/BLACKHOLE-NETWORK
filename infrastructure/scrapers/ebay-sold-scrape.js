#!/usr/bin/env node
// BHN eBay sold-comps scraper — core module.
// Scrapes eBay sold listing search results pages (HTML, no API).
// Exports: { scrapeCard, scrapeSet }
//
// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  DEPLOYMENT RULES — NON-NEGOTIABLE                                          ║
// ║  • This scraper MUST only run on LA (10.8.0.1).                             ║
// ║  • NEVER run from the operator's home IP — eBay will associate the IP       ║
// ║    with any personal account activity on that machine.                      ║
// ║  • NEVER authenticate to eBay from LA. Guest-only sessions. No cookies,    ║
// ║    no login, no eBay account credentials anywhere in this pipeline.         ║
// ║  • Set  BHN_RUN_ON_LA=1  in the LA environment to unlock execution.        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝
//
// Usage (single card):
//   node ebay-sold-scrape.js --card "Dark Weezing" --set "Team Rocket" \
//     --card-number 14 --edition "1st Edition" --print-variant Holo [--dry-run]
// (--variant kept as deprecated alias for --print-variant)
//
// Stealth rules (non-negotiable):
//   Base delay: 8–15s randomised · Long pause every 10–15 req: 25–45s
//   Full break every ~50 req: 3–5 min · 3 consecutive rate-limits → stop

'use strict';

// ── Deployment guard ───────────────────────────────────────────────────────────
// Blocks execution unless BHN_RUN_ON_LA=1 is set in the environment.
// This variable is present on the LA server and MUST NOT be set on the operator PC.
// --force-local bypasses the check for offline unit-testing only (no real requests).
function assertRunningOnLA() {
  const isApproved  = process.env.BHN_RUN_ON_LA === '1';
  const forceLocal  = process.argv.includes('--force-local');
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
}

const fs   = require('fs');
const path = require('path');

// ── Constants ──────────────────────────────────────────────────────────────────

const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15',
];

const BROWSER_HEADERS = {
  Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.5',
  'Accept-Encoding': 'gzip, deflate, br',
  Connection: 'keep-alive',
  'Upgrade-Insecure-Requests': '1',
  'Sec-Fetch-Dest': 'document',
  'Sec-Fetch-Mode': 'navigate',
  'Sec-Fetch-Site': 'none',
  'Cache-Control': 'max-age=0',
  // Explicitly NO Cookie header — guest-only sessions, never authenticated.
  // If a Cookie somehow appears in the environment, it must never reach eBay.
};

const SET_MAP = {
  'Base Set':                  { code: 'BST', year: '1999' },
  'Fossil':                    { code: 'FSL', year: '1999' },
  'Jungle':                    { code: 'JGL', year: '1999' },
  'Team Rocket':               { code: 'TRK', year: '2000' },
  'Gym Heroes':                { code: 'GYH', year: '2000' },
  'Gym Challenge':             { code: 'GYC', year: '2000' },
  'Wizards Black Star Promos': { code: 'WSP', year: '1999' },
  'Best of Game':              { code: 'BOG', year: '2002' },
};

const EDITION_MAP = {
  '1st Edition': '1E',
  'Unlimited':   'UN',
  'Shadowless':  'SH',
  'N/A':         'NA',
};

const VARIANT_MAP = {
  'Standard':           null,
  'Holo':               'HOL',
  'Error':              'ERR',
  'No Symbol':          'NOS',
  'W Stamp':            'WST',
  'Winner':             'WIN',
  'Jumbo':              'JMB',
  'Prerelease':         'PRE',
  'Gold Border':        'GLB',
  'Red Cheeks':         'RCK',
  'WB Movie':           'WBM',
  'Nintendo Power':     'NTP',
  'WOTC':               'WTC',
  '1999-2000 Copyright':'C99',
};

// CGC tier labels that appear before a grade number in listing titles
const CGC_TIER_LABELS = ['Perfect', 'Pristine', 'Gem Mint', 'Mint+', 'Mint', 'Near Mint-Mint+',
  'Near Mint-Mint', 'Near Mint+', 'Near Mint', 'Excellent-Mint+', 'Excellent-Mint',
  'Excellent+', 'Excellent', 'Very Good-Excellent+', 'Very Good-Excellent',
  'Very Good+', 'Very Good', 'Good+', 'Good', 'Fair', 'Poor'];

const BGS_SGC_TIER_LABELS = ['Pristine', 'Gem Mint', 'Mint+', 'Mint',
  'Near Mint-Mint+', 'Near Mint-Mint', 'Near Mint+', 'Near Mint',
  'Excellent-Mint+', 'Excellent-Mint', 'Excellent+', 'Excellent',
  'Very Good-Excellent+', 'Very Good-Excellent', 'Very Good+', 'Very Good',
  'Good+', 'Good', 'Fair', 'Poor', 'Authentic'];

// ── Helpers ────────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Randomised delay (never fixed)
const randMs  = (minS, maxS) => (minS + Math.random() * (maxS - minS)) * 1000;
const randInt = (min, max)   => min + Math.floor(Math.random() * (max - min + 1));

function pickSessionUA() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

// Build PBDS code: TRK014-2000-1E-HOL
function buildPbdsCode(set_name, card_number, edition, print_variant) {
  const setInfo = SET_MAP[set_name];
  if (!setInfo) return null;
  const num     = String(card_number).padStart(3, '0');
  const edCode  = EDITION_MAP[edition] || 'NA';
  const varCode = VARIANT_MAP[print_variant];
  const parts   = [setInfo.code + num, setInfo.year, edCode];
  if (varCode) parts.push(varCode);
  return parts.join('-');
}

// Build eBay sold-listings search URL for one card, one page
function buildSearchUrl(cardDef, page) {
  const query = `pokemon ${cardDef.set_name} 1st edition graded PSA CGC ${cardDef.card_name}`;
  const qs = new URLSearchParams({
    _nkw:        query,
    _sacat:      '0',
    LH_Sold:     '1',
    LH_Complete: '1',
    _sop:        '13',
    rt:          'nc',
    _pgn:        String(page),
  });
  return `https://www.ebay.com/sch/i.html?${qs}`;
}

// Parse grader + grade from listing title.
// PSA   → bare number ("10", "9", "8.5")
// CGC 1–9 → bare number; CGC 10 → tier label required ("Gem Mint 10") or return null grade
// BGS/SGC → tier label + number ("Gem Mint 9.5")
// Returns: { grader: string|null, grade: string|null, gradeLabel: string|null }
function parseGradeFromTitle(title) {
  if (!title) return { grader: null, grade: null, gradeLabel: null };
  const t = title.toUpperCase();

  let grader = null;
  if (t.includes('PSA'))      grader = 'PSA';
  else if (t.includes('CGC')) grader = 'CGC';
  else if (t.includes('BGS')) grader = 'BGS';
  else if (t.includes('SGC')) grader = 'SGC';
  if (!grader) return { grader: null, grade: null, gradeLabel: null };

  if (grader === 'PSA') {
    // PSA: extract bare number after "PSA" keyword
    const m = title.match(/\bPSA\s+(\d+\.?\d*)\b/i);
    if (m) return { grader, grade: m[1], gradeLabel: null };
    // PSA with tier name: "PSA Gem Mint 10" → extract trailing number
    const m2 = title.match(/\bPSA\b.*?(\d+\.?\d*)\s*(?:$|[^/\d])/i);
    if (m2) return { grader, grade: m2[1], gradeLabel: null };
    return { grader, grade: null, gradeLabel: null };
  }

  if (grader === 'CGC') {
    // Try to find tier + number combination (longest tier first)
    for (const tier of CGC_TIER_LABELS) {
      const re = new RegExp(`\\b${tier}\\s+(\\d+\\.?\\d*)\\b`, 'i');
      const m = title.match(re);
      if (m) {
        const num   = m[1];
        const label = `${tier} ${num}`;  // e.g. "Gem Mint 10", "Mint+ 9.5"
        // For grades 1-9, raw_label in catalog is bare number; for 10 it's the full tier label
        const grade = parseFloat(num) < 10 ? num : label;
        return { grader, grade, gradeLabel: tier };
      }
    }
    // Bare number after CGC (no tier label)
    const m = title.match(/\bCGC\s+(\d+\.?\d*)\b/i);
    if (m) {
      const num = m[1];
      // Bare CGC 10 is ambiguous — cannot determine tier without the physical slab
      if (num === '10') return { grader, grade: null, gradeLabel: null };
      return { grader, grade: num, gradeLabel: null };
    }
    return { grader, grade: null, gradeLabel: null };
  }

  // BGS / SGC: always tier-named in the catalog (e.g. "Gem Mint 9.5")
  // Try both orders: TIER NUMBER and NUMBER TIER
  const tierLabels = BGS_SGC_TIER_LABELS;
  for (const tier of tierLabels) {
    // Forward: "Gem Mint 9.5"
    const re = new RegExp(`\\b${tier}\\s+(\\d+\\.?\\d*)\\b`, 'i');
    const m = title.match(re);
    if (m) {
      return { grader, grade: `${tier} ${m[1]}`, gradeLabel: tier };
    }
    // Reverse: "BGS 9.5 Gem Mint" — number comes before tier label
    const graderRe = new RegExp(`\\b${grader}\\s+(\\d+\\.?\\d*)\\s+${tier}\\b`, 'i');
    const m2 = title.match(graderRe);
    if (m2) {
      return { grader, grade: `${tier} ${m2[1]}`, gradeLabel: tier };
    }
  }
  // Fallback: bare number after grader keyword (rare for BGS/SGC but defensively handle)
  const fallback = title.match(new RegExp(`\\b${grader}\\s+(\\d+\\.?\\d*)\\b`, 'i'));
  if (fallback) return { grader, grade: fallback[1], gradeLabel: null };
  return { grader, grade: null, gradeLabel: null };
}

// Parse a sold price string → { price: number|null, currency: string }
function parsePriceAndCurrency(raw) {
  if (!raw) return { price: null, currency: 'USD' };
  const s = String(raw).trim();
  let currency = 'USD';
  if (/^C\s*\$/.test(s) || /CAD/.test(s)) currency = 'CAD';
  else if (/^£/.test(s) || /GBP/.test(s))  currency = 'GBP';
  const num = parseFloat(s.replace(/[^0-9.]/g, ''));
  return { price: isNaN(num) ? null : num, currency };
}

// Safely get text from a cheerio selector (first match)
function safeText($el) {
  try { return $el.first().text().trim() || null; } catch { return null; }
}

// Parse listing tiles from a cheerio-loaded eBay search page
function parseListings($, cardDef) {
  const cheerio = require('cheerio');  // already required at call site but re-require is safe
  const results = [];

  $('li.s-item, div.s-item').each((_, el) => {
    try {
      const $el = $(el);

      // Skip eBay placeholder tiles
      const titleEl = $el.find('.s-item__title, h3.s-item__title').first();
      const titleText = titleEl.text().trim();
      if (!titleText || titleText === 'Shop on eBay') return;

      // Listing URL + item ID
      const linkHref = $el.find('a.s-item__link').first().attr('href') || '';
      const itemIdMatch = linkHref.match(/\/itm\/(\d+)/);
      const itemId = itemIdMatch ? itemIdMatch[1] : null;
      if (!itemId) return;

      const listingUrl = itemId ? `https://www.ebay.com/itm/${itemId}` : null;

      // Price + currency
      const priceRaw = safeText($el.find('.s-item__price'));
      const { price: soldPrice, currency } = parsePriceAndCurrency(priceRaw);

      // Shipping
      const shippingRaw = safeText(
        $el.find('.s-item__shipping, .s-item__logisticsCost')
      );
      let shipping = null;
      if (shippingRaw) {
        if (/free/i.test(shippingRaw)) {
          shipping = 0;
        } else {
          const sm = shippingRaw.match(/(\d+\.?\d*)/);
          if (sm) shipping = parseFloat(sm[1]);
        }
      }

      // Sold date
      const soldDateRaw = safeText(
        $el.find('.s-item__caption--signal, .s-item__title--tag, .s-item__endedDate')
      );
      let createdAt = null;
      if (soldDateRaw) {
        const stripped = soldDateRaw.replace(/^sold\s*/i, '').trim();
        const d = new Date(stripped);
        if (!isNaN(d.getTime())) createdAt = d.toISOString();
      }

      // Bid count (auction format)
      const bidRaw = safeText($el.find('.s-item__bids, .s-item__bidCount'));
      let bidCount = null;
      if (bidRaw) {
        const bm = bidRaw.match(/(\d+)/);
        if (bm) bidCount = parseInt(bm[1], 10);
      }

      // Transaction type
      let transactionType = 'Buy It Now';
      if (bidCount && bidCount > 0) transactionType = 'Auction';
      const bofText = safeText($el.find('.s-item__detail--secondary')) || '';
      if (/best offer/i.test(bofText) || /best offer/i.test(titleText)) {
        transactionType = 'Best Offer';
      }

      // Condition
      const condition = safeText($el.find('.SECONDARY_INFO, .s-item__subtitle'));

      // Location
      const location = safeText($el.find('.s-item__itemLocation'));

      // Seller (often absent on search page)
      const sellerRaw = safeText($el.find('.s-item__seller-info, .mbg-nw'));
      let seller = null;
      if (sellerRaw) {
        const sm2 = sellerRaw.match(/([a-z0-9_\-\.]+)/i);
        if (sm2) seller = sm2[1];
      }

      // Grade parsing from title
      const { grader, grade, gradeLabel } = parseGradeFromTitle(titleText);

      // Card number parsing from title (fallback to cardDef)
      let card_number = cardDef.card_number;
      const cnMatch = titleText.match(/#(\d+)(?:\/\d+)?/);
      if (cnMatch) card_number = cnMatch[1];

      // PBDS code
      const edition       = cardDef.edition       || '1st Edition';
      const print_variant = cardDef.print_variant  || 'Standard';
      const pbds_code = buildPbdsCode(
        cardDef.set_name, card_number, edition, print_variant
      );

      results.push({
        pbds_code,
        item_id:            itemId,
        title:              titleText,
        card_name:          cardDef.card_name,
        set_name:           cardDef.set_name,
        card_number,
        edition,
        print_variant,
        grader:             grader || null,
        grade:              grade  || null,
        grade_label:        gradeLabel || null,
        sold_price:         soldPrice,
        currency,
        shipping:           shipping !== null ? shipping : null,
        transaction_type:   transactionType,
        bid_count:          bidCount,
        created_at:         createdAt,
        seller:             seller,
        seller_feedback:    null,
        seller_feedback_pct: null,
        cert_number:        null,  // Option A: never fetch individual pages
        location:           location ? location.replace(/^from\s*/i,'').trim() : null,
        condition,
        returns_accepted:   null,
        obo_min_price:      null,
        current_bid:        bidCount ? soldPrice : null,
        watchers:           null,
        listing_url:        listingUrl,
      });
    } catch (e) {
      // Defensive: skip malformed tiles silently
    }
  });

  return results;
}

// Fetch one URL with full browser-like headers; return response text or throw.
// Cookie header is explicitly deleted — guest-only sessions, never authenticated.
async function fetchPage(url, sessionUA, timeoutMs = 25000) {
  const ac  = new AbortController();
  const tid = setTimeout(() => ac.abort(), timeoutMs);
  const headers = { ...BROWSER_HEADERS, 'User-Agent': sessionUA };
  delete headers['Cookie'];   // belt-and-suspenders: no auth cookies ever sent to eBay
  delete headers['cookie'];
  try {
    const res = await fetch(url, {
      signal: ac.signal,
      headers,
      redirect: 'follow',
      credentials: 'omit',    // Node fetch: omit any credential storage
    });
    const text = await res.text();
    return { status: res.status, text };
  } finally {
    clearTimeout(tid);
  }
}

// Detect rate-limiting from status or HTML content
function isRateLimited(status, html) {
  if (status === 429 || status === 503) return true;
  if (!html) return false;
  const lower = html.toLowerCase();
  return lower.includes('captcha') || lower.includes('robot') ||
    lower.includes('access denied') || lower.includes('unusual traffic');
}

// ── Checkpoint helpers ─────────────────────────────────────────────────────────

function checkpointPath(dir, setCode) {
  return path.join(dir, `checkpoint-${setCode}.json`);
}

function loadCheckpoint(dir, setCode) {
  const p = checkpointPath(dir, setCode);
  try {
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {}
  return null;
}

function saveCheckpoint(dir, setCode, state) {
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(checkpointPath(dir, setCode), JSON.stringify({ ...state, savedAt: new Date().toISOString() }, null, 2));
  } catch (e) {
    console.error(`[checkpoint] save failed: ${e.message}`);
  }
}

// ── Core scrape functions ──────────────────────────────────────────────────────

// Scrape one card definition — up to config.max_pages_per_card pages.
// Returns array of raw result objects (not yet DB-inserted).
async function scrapeCard(cardDef, config, callbacks, sessionUA, reqState) {
  const { onLog, onStats, isPaused, shouldSkip } = callbacks;

  const setInfo  = SET_MAP[cardDef.set_name] || {};
  const setCode  = setInfo.code || 'UNK';
  const cardCode = `${setCode}${String(cardDef.card_number).padStart(3,'0')}`;

  const results = [];
  let pagesDone = 0;

  for (let page = 1; page <= (config.max_pages_per_card || 3); page++) {
    // Poll pause/skip flags between pages
    while (isPaused && isPaused()) await sleep(500);
    if (shouldSkip && shouldSkip()) {
      onLog && onLog(`[SKIP] ${cardCode} — skipped by operator`, 'info');
      return results;
    }

    const url = buildSearchUrl(cardDef, page);
    const multiplier = (reqState && reqState.delayMultiplier) || 1.0;

    // Cold-start warmup — fires ONCE per process, before the very first eBay request.
    // Critical: prevents hammering a possibly-already-rate-limited IP immediately on resume.
    // Per feedback_ebay_scraper_never_auto_kickoff: a sub-15s base delay is NOT a warmup.
    if (!reqState.warmupDone) {
      const warmupMs = randMs(
        config.initial_warmup_min_sec || 120,
        config.initial_warmup_max_sec || 180
      ) * multiplier;
      onLog && onLog(`[WARMUP] cold-start pause ${Math.round(warmupMs/1000)}s before first eBay request`, 'info');
      await sleep(warmupMs);
      reqState.warmupDone = true;
    }

    // Stealth delay before every request
    await sleep(randMs(config.delay_min_sec, config.delay_max_sec) * multiplier);

    // Long pause every 10–15 requests (gate dropped — also evaluates on request 1)
    reqState.count = (reqState.count || 0) + 1;
    if (reqState.count % reqState.longPauseEvery === 0) {
      const pauseMs = randMs(config.long_pause_min_sec, config.long_pause_max_sec) * multiplier;
      onLog && onLog(`[PAUSE] long pause ${Math.round(pauseMs/1000)}s`, 'info');
      onStats && onStats({ pauses: 1 });
      await sleep(pauseMs);
      reqState.longPauseEvery = randInt(10, 15);  // re-randomise interval
    }

    // Full break every ~50 requests (gate dropped — also evaluates on request 1)
    if (reqState.count % (config.break_every_n || 50) === 0) {
      const breakMs = randMs(
        (config.break_min_min || 3) * 60,
        (config.break_max_min || 5) * 60
      ) * multiplier;
      onLog && onLog(`[BREAK] full break ${Math.round(breakMs/1000)}s`, 'warn');
      onStats && onStats({ breaks: 1 });
      await sleep(breakMs);
    }

    let status, html;
    try {
      // [FETCH] log placed here so its timestamp reflects the actual HTTP request,
      // not the queue intent (this fires AFTER warmup + base-delay + any pause/break).
      onLog && onLog(`[FETCH] ${cardCode} p${page} — ${url.slice(0, 80)}…`, 'debug');
      ({ status, text: html } = await fetchPage(url, sessionUA));
      onStats && onStats({ requests: 1 });
    } catch (e) {
      onLog && onLog(`[ERROR] ${cardCode} p${page} fetch failed: ${e.message}`, 'error');
      break;
    }

    if (isRateLimited(status, html)) {
      reqState.consecutiveBlocks = (reqState.consecutiveBlocks || 0) + 1;
      const backoffMs = randMs(10 * 60, 20 * 60) * multiplier;
      onLog && onLog(
        `[BACKOFF] ${status} detected (${reqState.consecutiveBlocks}/3) — pausing ${Math.round(backoffMs/1000)}s`,
        'warn'
      );
      onStats && onStats({ rateLimitHits: 1 });
      if (reqState.consecutiveBlocks >= 3) {
        onLog && onLog('[STOP] 3 consecutive blocks — halting scraper', 'error');
        reqState.shouldStop = true;
        return results;
      }
      await sleep(backoffMs);
      page--;  // retry same page after backoff
      continue;
    }

    reqState.consecutiveBlocks = 0;

    // Parse with cheerio
    let cheerio;
    try { cheerio = require('cheerio'); } catch { throw new Error('cheerio not installed — run npm install'); }
    const $ = cheerio.load(html);
    const pageResults = parseListings($, cardDef);

    if (pageResults.length === 0) {
      onLog && onLog(`[DONE] ${cardCode} p${page} — 0 results, stopping pagination`, 'debug');
      break;
    }

    results.push(...pageResults);
    pagesDone++;

    onLog && onLog(
      `[CARD] ${cardCode} p${page} — ${pageResults.length} results (total: ${results.length})`,
      'debug'
    );

    onStats && onStats({ rowsCaptured: pageResults.length });
  }

  // Summarise price bands for the live feed line
  if (results.length > 0) {
    const priceSummary = buildPriceSummary(results);
    onLog && onLog(
      `[${cardCode}] ${cardDef.card_name} — ${results.length} results${priceSummary}`,
      'result'
    );
  } else {
    onLog && onLog(`[${cardCode}] ${cardDef.card_name} — 0 results`, 'result');
    onStats && onStats({ zeroResults: 1 });
  }

  return results;
}

// Build a compact price summary string: "| PSA10: $399 | PSA9: $280"
function buildPriceSummary(results) {
  const buckets = {};
  for (const r of results) {
    if (!r.grader || !r.grade || !r.sold_price) continue;
    const key = `${r.grader}${r.grade}`;
    if (!buckets[key]) buckets[key] = [];
    buckets[key].push(r.sold_price);
  }
  const parts = Object.entries(buckets)
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(0, 4)
    .map(([k, prices]) => {
      const avg = Math.round(prices.reduce((s, p) => s + p, 0) / prices.length);
      return `${k}: $${avg}`;
    });
  return parts.length ? ' | ' + parts.join(' | ') : '';
}

// Scrape a set of cards with checkpoint/resume.
// cards: array of { card_name, card_number, set_name, edition, print_variant }
// callbacks: { onResult(row), onLog, onStats, onCheckpoint, isPaused, shouldSkip }
async function scrapeSet(cards, config, callbacks, checkpointDir, seenItemIds) {
  checkpointDir = checkpointDir || path.join(process.cwd(), 'checkpoints');
  seenItemIds   = seenItemIds instanceof Set ? seenItemIds : new Set(seenItemIds || []);

  const sessionUA = pickSessionUA();
  const setCode   = (SET_MAP[cards[0]?.set_name] || {}).code || 'UNK';

  callbacks.onLog && callbacks.onLog(`[SESSION] UA: ${sessionUA.slice(0, 40)}…`, 'info');

  // Load checkpoint if present
  let checkpoint = loadCheckpoint(checkpointDir, setCode);
  const cardsDone = new Set(checkpoint ? checkpoint.cardsDone || [] : []);
  if (checkpoint) {
    callbacks.onLog && callbacks.onLog(
      `[RESUME] Checkpoint found — ${cardsDone.size} cards done, resuming`, 'info'
    );
    // Merge previously-seen item IDs
    for (const id of (checkpoint.processedItemIds || [])) seenItemIds.add(id);
  }

  const reqState = {
    count:             0,
    longPauseEvery:    randInt(10, 15),
    consecutiveBlocks: 0,
    delayMultiplier:   1.0,
    shouldStop:        false,
  };

  // Allow driver to share delayMultiplier via reqState reference
  if (callbacks.reqState) Object.assign(reqState, callbacks.reqState);

  let cardsDoneThisRun = 0;

  for (const card of cards) {
    const setInfo  = SET_MAP[card.set_name] || {};
    const setCode2 = setInfo.code || 'UNK';
    const cardKey  = `${setCode2}${String(card.card_number).padStart(3,'0')}`;

    if (cardsDone.has(cardKey)) {
      callbacks.onLog && callbacks.onLog(`[SKIP] ${cardKey} already in checkpoint`, 'debug');
      callbacks.onStats && callbacks.onStats({ cardsSkipped: 1 });
      continue;
    }

    if (reqState.shouldStop) break;

    const rawResults = await scrapeCard(card, config, callbacks, sessionUA, reqState);

    // Dedup against seenItemIds (covers existing DB rows for TRK supplement mode)
    let newRows = 0;
    for (const row of rawResults) {
      if (!row.item_id || seenItemIds.has(row.item_id)) {
        callbacks.onStats && callbacks.onStats({ rowsSkipped: 1 });
        continue;
      }
      seenItemIds.add(row.item_id);
      newRows++;
      if (callbacks.onResult) await callbacks.onResult(row);
    }
    callbacks.onStats && callbacks.onStats({ rowsCaptured: newRows });

    cardsDone.add(cardKey);
    cardsDoneThisRun++;

    // Save checkpoint every N cards
    if (cardsDoneThisRun % (config.checkpoint_every_n_cards || 10) === 0) {
      saveCheckpoint(checkpointDir, setCode, {
        setCode,
        lastCard: cardKey,
        cardsDone: [...cardsDone],
        processedItemIds: [...seenItemIds],
      });
      callbacks.onCheckpoint && callbacks.onCheckpoint({ cardKey, cardsDone: cardsDone.size });
      callbacks.onLog && callbacks.onLog(
        `[CHECKPOINT] Saved at ${cardKey} — ${cardsDone.size} cards done`, 'info'
      );
    }

    // Hot-reload config between cards (not mid-card)
    if (callbacks.configPath) {
      const fresh = hotReloadConfig(callbacks.configPath);
      if (fresh) Object.assign(config, fresh);
    }
  }

  // Final checkpoint
  saveCheckpoint(checkpointDir, setCode, {
    setCode,
    lastCard: [...cardsDone].pop() || '',
    cardsDone: [...cardsDone],
    processedItemIds: [...seenItemIds],
  });

  return { cardsDone: cardsDone.size, seenItemIds };
}

function hotReloadConfig(configPath) {
  try {
    return JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch {
    return null;
  }
}

// ── CLI entry point ────────────────────────────────────────────────────────────

module.exports = {
  scrapeCard,
  scrapeSet,
  buildPbdsCode,
  buildSearchUrl,
  parseGradeFromTitle,
  parsePriceAndCurrency,
  parseListings,
  hotReloadConfig,
  pickSessionUA,
};

async function main() {
  assertRunningOnLA();

  const args   = process.argv.slice(2);
  const flag   = (k) => { const i = args.indexOf(k); return i >= 0 && args[i+1] ? args[i+1] : null; };
  const hasF   = (k) => args.includes(k);

  const cardName    = flag('--card');
  const setName     = flag('--set');
  const cardNumber  = flag('--card-number');
  const edition     = flag('--edition')  || '1st Edition';
  const printVariant = flag('--print-variant') || flag('--variant') || 'Standard';
  const dryRun      = hasF('--dry-run');
  const configPath  = flag('--config')   || path.join(__dirname, 'ebay_scraper_config.json');
  const checkDir    = flag('--checkpoint-dir') || path.join(__dirname, 'checkpoints');

  if (!cardName || !setName) {
    console.error('usage: ebay-sold-scrape.js --card <name> --set <set> [--card-number N] [--edition <ed>] [--print-variant <v>] [--dry-run]');
    process.exit(2);
  }

  const config = hotReloadConfig(configPath) || {
    initial_warmup_min_sec: 120, initial_warmup_max_sec: 180,
    delay_min_sec: 8, delay_max_sec: 15,
    long_pause_every_n: 12, long_pause_min_sec: 25, long_pause_max_sec: 45,
    break_every_n: 50, break_min_min: 3, break_max_min: 5,
    max_pages_per_card: 3, checkpoint_every_n_cards: 10,
  };

  const cardDef = { card_name: cardName, set_name: setName, card_number: cardNumber || '0', edition, print_variant: printVariant };
  const sessionUA = pickSessionUA();
  const reqState  = { count: 0, longPauseEvery: 12, consecutiveBlocks: 0, delayMultiplier: 1.0, shouldStop: false, warmupDone: false };

  const callbacks = {
    onLog:    (msg, level) => console.log(`[${level || 'info'}] ${msg}`),
    onStats:  (delta) => {},
    onResult: (row) => {
      if (dryRun) {
        console.log(`  ${row.pbds_code || '?'} | ${row.grader || 'RAW'} ${row.grade || ''} | $${row.sold_price || '?'} | ${row.title?.slice(0,60)}`);
      }
    },
    onCheckpoint: (s) => {},
    isPaused:     () => false,
    shouldSkip:   () => false,
  };

  console.log(`Scraping: ${cardName} (${setName}${cardNumber ? ' #'+cardNumber : ''})${dryRun ? ' [DRY-RUN]' : ''}`);
  const results = await scrapeCard(cardDef, config, callbacks, sessionUA, reqState);
  console.log(`Done. ${results.length} results.`);
  if (dryRun) console.log('[DRY-RUN] No data written.');
}

if (require.main === module) {
  main().catch((e) => { console.error('fatal:', e); process.exit(1); });
}
