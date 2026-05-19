#!/usr/bin/env node
// CGC Pokémon population-report scraper. Hits the canonical JSON API:
//   https://production.api.aws.ccg-ops.com/api/cards/research/trading-cards/population/
// Paginates with `page=N` until an empty Items array is returned. No browser, no scraping —
// the public population-report pages render this same payload client-side.
//
// Run as CLI for one set:
//   node cgc-pop-scrape.js --name "Team Rocket 1st Edition" --id 16892 [--population-id 471423]
//
// Or as a module (see cgc-pop-scrape-all.js):
//   const { scrapeSet } = require('./cgc-pop-scrape');

const fs = require('fs');
const path = require('path');

const API = 'https://production.api.aws.ccg-ops.com/api/cards/research/trading-cards/population/';
const GRADER = 'CGC';
const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const PAGE_DELAY_MS = 600; // be polite to the API
const REQUEST_TIMEOUT_MS = 20000;

// Map API population_* fields to the grade labels already in pop_reports (sourced from the
// DOM table headers on the public page). The labels MUST match — they're part of the upsert key.
const GRADE_MAP = {
  population_Perfect10: 'Perfect 10',
  population_Pristine10: 'Pristine 10',
  population_GemMint10: 'Gem Mint 10',
  population_9_5: 'Mint+ 9.5',
  population_9_0: '9',
  population_8_5: '8.5',
  population_8_0: '8',
  population_7_5: '7.5',
  population_7_0: '7',
  population_6_5: '6.5',
  population_6_0: '6',
  population_5_5: '5.5',
  population_5_0: '5',
  population_4_5: '4.5',
  population_4_0: '4',
  population_3_5: '3.5',
  population_3_0: '3',
  population_2_5: '2.5',
  population_2_0: '2',
  population_1_5: '1.5',
  population_1_0: '1',
  population_AU: 'AU',
  population_AA: 'AA',
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function slugify(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function buildUrl(researchGroupID, populationID, page) {
  const qs = new URLSearchParams();
  qs.set('researchGroupID', String(researchGroupID));
  qs.set('page', String(page));
  if (populationID) qs.set('populationID', String(populationID));
  return `${API}?${qs.toString()}`;
}

// Reconstruct the row-name format used by the public table view:
//   "<name> (<cardYear>) <variant> <description>"
//   e.g. "Dark Alakazam (2000) Holo Rare"
function composeCardName(item) {
  const parts = [];
  parts.push(item.cardYear ? `${item.name} (${item.cardYear})` : item.name);
  if (item.variant) parts.push(item.variant);
  if (item.description) parts.push(item.description);
  return parts.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}

async function fetchPage(url) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: ac.signal,
      headers: {
        'User-Agent': USER_AGENT,
        Accept: 'application/json',
        Origin: 'https://www.cgccards.com',
        Referer: 'https://www.cgccards.com/',
      },
    });
    if (!res.ok) throw new Error(`http ${res.status} for ${url}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

async function scrapeSet({ name, researchGroupID, populationID, outPath, maxPages = 100 }) {
  if (!name || !researchGroupID) {
    throw new Error('scrapeSet: name and researchGroupID required');
  }
  const out = outPath || path.join(__dirname, `${slugify(name)}.json`);

  const records = [];
  const seenIds = new Set();
  const scrapedAt = new Date().toISOString();
  let stopReason = 'max-pages';
  // Filled from the first page's response envelope (TotalCount/PageCount). Used for the
  // completeness assertion at the end. Null if the API didn't return it.
  let expectedTotal = null;
  let expectedPages = null;

  for (let page = 1; page <= maxPages; page++) {
    const url = buildUrl(researchGroupID, populationID, page);
    let data;
    try {
      data = await fetchPage(url);
    } catch (e) {
      console.error(`[${name}][page ${page}] fetch error: ${e.message}`);
      stopReason = `fetch-error-page-${page}`;
      break;
    }
    if (page === 1) {
      if (typeof data.TotalCount === 'number') expectedTotal = data.TotalCount;
      if (typeof data.PageCount === 'number') expectedPages = data.PageCount;
      console.error(
        `[${name}][page 1] envelope: TotalCount=${expectedTotal} PageCount=${expectedPages} PageSize=${data.PageSize}`
      );
    }
    const items = Array.isArray(data.Items) ? data.Items : [];
    if (items.length === 0) {
      console.error(`[${name}][page ${page}] empty Items — stopping`);
      stopReason = `empty-page-${page}`;
      break;
    }

    let newOnPage = 0;
    for (const item of items) {
      // populationID is the per-card key in the API response. Dedup across pages on it.
      const key = item.populationID ?? `${item.cardNumber}|${item.name}|${item.variant}`;
      if (seenIds.has(key)) continue;
      seenIds.add(key);
      newOnPage++;

      const card_name = composeCardName(item);
      const card_number = item.cardNumber || '';
      for (const [field, grade] of Object.entries(GRADE_MAP)) {
        const pop = item[field];
        if (typeof pop !== 'number' || pop === 0) continue;
        records.push({
          set: name,
          card_name,
          card_number,
          grade,
          population: pop,
          grader: GRADER,
          source_url: url,
          scraped_at: scrapedAt,
        });
      }
    }
    console.error(
      `[${name}][page ${page}] items=${items.length} new_cards=${newOnPage} records=${records.length}`
    );
    if (newOnPage === 0) {
      stopReason = `dup-page-${page}`;
      console.error(`[${name}][page ${page}] no new cards — stopping`);
      break;
    }
    await sleep(PAGE_DELAY_MS);
  }

  fs.writeFileSync(out, JSON.stringify(records, null, 2));

  // Completeness assertion: did we capture every item the API said exists?
  // expectedTotal may be null if the envelope didn't include it (older deployments).
  const completenessOk =
    expectedTotal === null ? null : seenIds.size === expectedTotal;
  const completenessNote =
    completenessOk === null
      ? 'no TotalCount in response'
      : completenessOk
      ? 'OK'
      : `MISMATCH: got ${seenIds.size} / expected ${expectedTotal} (delta=${seenIds.size - expectedTotal})`;
  if (completenessOk === false) {
    console.error(`[${name}] completeness WARN: ${completenessNote}`);
  }

  const summary = {
    name,
    out,
    unique_cards: seenIds.size,
    total_records: records.length,
    stop_reason: stopReason,
    expected_total: expectedTotal,
    expected_pages: expectedPages,
    completeness_ok: completenessOk,
  };
  console.error(
    `[${name}] done. stop=${stopReason} unique=${seenIds.size}` +
      `${expectedTotal !== null ? `/${expectedTotal}` : ''} records=${records.length} completeness=${completenessNote} -> ${out}`
  );
  return summary;
}

module.exports = { scrapeSet, buildUrl, composeCardName, GRADE_MAP };

async function main() {
  const args = process.argv.slice(2);
  const flag = (k) => {
    const i = args.indexOf(k);
    return i >= 0 && args[i + 1] ? args[i + 1] : null;
  };
  const name = flag('--name');
  const id = flag('--id');
  if (!name || !id) {
    console.error(
      'usage: cgc-pop-scrape.js --name "Set Name" --id researchGroupID [--population-id N] [--out file.json]'
    );
    process.exit(2);
  }
  await scrapeSet({
    name,
    researchGroupID: parseInt(id, 10),
    populationID: flag('--population-id'),
    outPath: flag('--out'),
  });
}

if (require.main === module) {
  main().catch((e) => {
    console.error('fatal:', e);
    process.exit(1);
  });
}
