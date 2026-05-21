#!/usr/bin/env node
// PSA Pokemon population-report scraper. DECOUPLED FETCH MODEL: runs anywhere (local/residential),
// emits JSON files, and never touches the LA DB — LA ingests the JSON via cgc-pop-load.js.
//
// PSA's pop pages sit behind a Cloudflare managed challenge and there is no public pop API, so we
// use a stealth browser to clear the challenge ONCE, then call the same JSON endpoint the page uses:
//   POST https://www.psacard.com/Pop/GetSetItems
//   body: draw=1&start=0&length=<n>&search=&headingID=<setID>&categoryID=156940&isPSADNA=false
// The fetch runs in the page origin so the cf_clearance cookie rides along. cf_clearance is
// persisted in ./_psa-profile so re-runs reuse the cleared session.
//
// The search queue is eventhorizon.card_catalog WHERE active=true (set_name, card_number). Sets are
// mapped to PSA heading ids via psa-sets.json (PSA slugs are not derivable from catalog names).
//
// Usage:
//   PGPASSWORD=... node psa-pop-scrape.js --out-dir ./out          # full run from card_catalog
//   PGPASSWORD=... node psa-pop-scrape.js --sets "Base Set,Fossil"  # subset of catalog sets
//   node psa-pop-scrape.js --heading 57801 --name "Base Set" --no-filter   # offline test, no DB
//
// Env: PGHOST(10.8.0.1) PGPORT(5432) PGDATABASE(eventhorizon) PGUSER(ehuser) PGPASSWORD PGSSLMODE
//      PSA_HEADLESS=true  -> headless 'new' (less reliable vs Cloudflare than headful)

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-extra');
const Stealth = require('puppeteer-extra-plugin-stealth');
puppeteer.use(Stealth());

const GRADER = 'PSA';
const OUT_DEFAULT = __dirname;
const PROFILE = path.join(__dirname, '_psa-profile');
const SETS_FILE = path.join(__dirname, 'psa-sets.json');
const BASE = 'https://www.psacard.com';
const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const SET_DELAY_MS = 2500;   // jittered politeness between set pages (CF likes human pacing)
const NAV_TIMEOUT_MS = 60000;

// PSA JSON pop fields -> verbatim grade label stored in pop_reports.grade. PSA has no 9.5.
// Qualifier columns (GradeNQ) are intentionally skipped — they're a separate qualified count.
const GRADE_MAP = {
  GradeN0: 'Authentic',
  Grade1: '1', Grade1_5: '1.5', Grade2: '2', Grade2_5: '2.5',
  Grade3: '3', Grade3_5: '3.5', Grade4: '4', Grade4_5: '4.5',
  Grade5: '5', Grade5_5: '5.5', Grade6: '6', Grade6_5: '6.5',
  Grade7: '7', Grade7_5: '7.5', Grade8: '8', Grade8_5: '8.5',
  Grade9: '9', Grade10: '10',
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jitter = (ms) => ms + Math.floor(Math.random() * ms * 0.4);
const slugify = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

// catalog '#4' -> '4'; PSA CardNumber is already bare ('4'). Match on the trimmed, #-stripped value.
const normNo = (s) => (s == null ? '' : String(s).trim().replace(/^#/, '').trim());

// PSA splits a card across varieties (Variety: '', '1st Edition', 'Base Set 1999-2000'). Fold the
// variety into the name so each variety is a distinct pop_reports row under the unique key.
function composeCardName(item) {
  const name = String(item.SubjectName || '').trim();
  const v = String(item.Variety || '').trim();
  return (v ? `${name} ${v}` : name).replace(/\s+/g, ' ').trim();
}

function setUrl({ year, slug, headingID }) {
  return `${BASE}/pop/tcg-cards/${year}/${slug}/${headingID}`;
}

async function clearChallenge(page, maxMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const title = await page.title().catch(() => '');
    if (title && !/just a moment|attention required|verifying/i.test(title)) return true;
    await sleep(2000);
  }
  return false;
}

// Pull every item for one PSA set via the GetSetItems endpoint, executed in the (CF-cleared) page
// origin. Returns { records: rawItems[], recordsTotal }.
async function fetchSetItems(page, { headingID, categoryID }) {
  const result = await page.evaluate(
    async ({ headingID, categoryID }) => {
      const post = (length) => {
        const body = new URLSearchParams({
          draw: '1', start: '0', length: String(length), search: '',
          headingID: String(headingID), categoryID: String(categoryID), isPSADNA: 'false',
        }).toString();
        return fetch('/Pop/GetSetItems', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body,
        }).then((r) => r.json());
      };
      // First a tiny call to learn recordsTotal, then one call sized to grab everything.
      const probe = await post(1);
      const total = probe.recordsTotal || 0;
      const full = await post(total + 50);
      return { recordsTotal: full.recordsTotal, data: full.data || [] };
    },
    { headingID, categoryID }
  );
  return result;
}

// Convert raw PSA items -> pop_reports records. `keep` is null (keep all) or a Set of normalized
// catalog card numbers to filter to.
function itemsToRecords(items, { setName, sourceUrl, scrapedAt, keep }) {
  const records = [];
  const cardsSeen = new Set();
  for (const item of items) {
    // Skip the synthetic TOTAL POPULATION summary row and any header/blank rows.
    if (!item || item.SpecID === 0 || item.CardNumber == null) continue;
    if (/total population/i.test(item.SubjectName || '')) continue;
    const cardNo = normNo(item.CardNumber);
    if (keep && !keep.has(cardNo)) continue;
    cardsSeen.add(`${cardNo}|${item.SpecID}`);
    const cardName = composeCardName(item);
    for (const [field, grade] of Object.entries(GRADE_MAP)) {
      const pop = item[field];
      if (typeof pop !== 'number' || pop === 0) continue;
      records.push({
        set: setName,
        card_name: cardName,
        card_number: cardNo,
        grade,
        population: pop,
        grader: GRADER,
        source_url: sourceUrl,
        scraped_at: scrapedAt,
      });
    }
  }
  return { records, uniqueCards: cardsSeen.size };
}

async function scrapeSet(page, { setName, mapping, keep, categoryID }) {
  const sourceUrl = setUrl({ ...mapping });
  const scrapedAt = new Date().toISOString();

  const resp = await page.goto(sourceUrl, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT_MS });
  const httpStatus = resp ? resp.status() : 0;
  if (!(await clearChallenge(page))) {
    throw new Error(`cloudflare challenge not cleared for ${setName} (${sourceUrl})`);
  }

  const { recordsTotal, data } = await fetchSetItems(page, {
    headingID: mapping.headingID,
    categoryID,
  });
  const { records, uniqueCards } = itemsToRecords(data, { setName, sourceUrl, scrapedAt, keep });

  // Completeness signals: did the API hand back as many rows as it claimed, and (when filtering)
  // which cataloged card numbers were never found on PSA?
  const apiComplete = recordsTotal === 0 ? null : data.filter((d) => d && d.SpecID !== 0).length >= recordsTotal - 1;
  let missingFromPsa = [];
  if (keep) {
    const found = new Set(records.map((r) => r.card_number));
    missingFromPsa = [...keep].filter((n) => !found.has(n));
  }

  return {
    setName,
    sourceUrl,
    httpStatus,
    recordsTotal,
    api_rows: data.length,
    unique_cards: uniqueCards,
    total_records: records.length,
    api_complete: apiComplete,
    missing_from_psa: missingFromPsa,
    records,
  };
}

// ---- catalog (queue) source ---------------------------------------------------------------

async function loadCatalogFromDb(setFilter) {
  const { Client } = require('pg');
  const PG = {
    host: process.env.PGHOST || '10.8.0.1',
    port: parseInt(process.env.PGPORT || '5432', 10),
    database: process.env.PGDATABASE || 'eventhorizon',
    user: process.env.PGUSER || 'ehuser',
    password: process.env.PGPASSWORD,
    ssl: process.env.PGSSLMODE === 'require' ? { rejectUnauthorized: false } : false,
  };
  if (!PG.password) throw new Error('PGPASSWORD required to read card_catalog (or use --catalog/--heading)');
  const client = new Client(PG);
  await client.connect();
  try {
    const res = await client.query(
      `SELECT set_name, card_number FROM card_catalog WHERE active = true`
    );
    return res.rows;
  } finally {
    await client.end();
  }
}

// Group catalog rows -> Map<set_name, Set<normalized card_number>>
function groupCatalog(rows) {
  const bySet = new Map();
  for (const r of rows) {
    const set = r.set_name;
    if (!bySet.has(set)) bySet.set(set, new Set());
    bySet.get(set).add(normNo(r.card_number));
  }
  return bySet;
}

// ---- main ---------------------------------------------------------------------------------

function parseArgs() {
  const a = process.argv.slice(2);
  const flag = (k) => {
    const i = a.indexOf(k);
    return i >= 0 && a[i + 1] && !a[i + 1].startsWith('--') ? a[i + 1] : null;
  };
  return {
    outDir: flag('--out-dir') || OUT_DEFAULT,
    setsArg: flag('--sets'),
    noFilter: a.includes('--no-filter'),
    catalogFile: flag('--catalog'),
  };
}

(async () => {
  const args = parseArgs();
  const cfg = JSON.parse(fs.readFileSync(SETS_FILE, 'utf8'));
  const categoryID = cfg.categoryID;
  fs.mkdirSync(args.outDir, { recursive: true });

  // Build the work list: [{ setName, mapping, keep }]
  // --no-filter needs no card numbers, so it skips the DB entirely and works straight from
  // psa-sets.json (useful for offline tests and full-set captures).
  let work = [];

  if (args.noFilter) {
    const wanted = args.setsArg
      ? args.setsArg.split(',').map((s) => s.trim())
      : Object.keys(cfg.sets).filter((s) => cfg.sets[s] && cfg.sets[s].headingID);
    for (const setName of wanted) {
      const mapping = cfg.sets[setName];
      if (!mapping || !mapping.headingID) {
        console.error(`[skip] no PSA mapping for set "${setName}" (add it to psa-sets.json)`);
        continue;
      }
      work.push({ setName, mapping, keep: null });
    }
  } else {
    const rows = args.catalogFile
      ? JSON.parse(fs.readFileSync(args.catalogFile, 'utf8'))
      : await loadCatalogFromDb();
    const bySet = groupCatalog(rows);
    const wanted = args.setsArg ? args.setsArg.split(',').map((s) => s.trim()) : [...bySet.keys()];

    for (const setName of wanted) {
      const mapping = cfg.sets[setName];
      if (!mapping || !mapping.headingID) {
        console.error(`[skip] no PSA mapping for set "${setName}" (add it to psa-sets.json)`);
        continue;
      }
      work.push({ setName, mapping, keep: bySet.get(setName) });
    }
  }

  if (work.length === 0) {
    console.error('nothing to scrape');
    process.exit(2);
  }

  const browser = await puppeteer.launch({
    headless: process.env.PSA_HEADLESS === 'true' ? 'new' : false,
    userDataDir: PROFILE,
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled', '--window-size=1366,900'],
  });
  const page = await browser.newPage();
  await page.setUserAgent(UA);
  await page.setViewport({ width: 1366, height: 900 });

  const summaries = [];
  let anyFail = false;
  for (const w of work) {
    try {
      const s = await scrapeSet(page, { ...w, categoryID });
      const outPath = path.join(args.outDir, `psa-${slugify(w.setName)}.json`);
      fs.writeFileSync(outPath, JSON.stringify(s.records, null, 2));
      summaries.push({ ...s, out: outPath, records: undefined });
      console.error(
        `[${w.setName}] http=${s.httpStatus} api_rows=${s.api_rows}/${s.recordsTotal} ` +
          `kept_cards=${s.unique_cards} records=${s.total_records} ` +
          `complete=${s.api_complete} missing=${s.missing_from_psa.length} -> ${outPath}`
      );
      if (s.total_records === 0) anyFail = true;
    } catch (e) {
      console.error(`[${w.setName}] FAILED: ${e.message}`);
      summaries.push({ setName: w.setName, error: e.message, total_records: 0 });
      anyFail = true;
    }
    await sleep(jitter(SET_DELAY_MS));
  }

  await browser.close();

  console.error('\n=== PSA scrape summary ===');
  for (const s of summaries) {
    if (s.error) { console.error(`  ${s.setName.padEnd(28)} ERROR: ${s.error}`); continue; }
    const miss = s.missing_from_psa && s.missing_from_psa.length
      ? `  missing[${s.missing_from_psa.length}]: ${s.missing_from_psa.slice(0, 8).join(',')}${s.missing_from_psa.length > 8 ? '…' : ''}`
      : '';
    console.error(
      `  ${s.setName.padEnd(28)} cards=${String(s.unique_cards).padStart(4)} ` +
        `records=${String(s.total_records).padStart(5)}  total=${s.recordsTotal}${miss}`
    );
  }
  process.exit(anyFail ? 1 : 0);
})().catch((e) => {
  console.error('fatal:', e);
  process.exit(1);
});
