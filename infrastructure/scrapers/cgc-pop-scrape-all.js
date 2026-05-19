#!/usr/bin/env node
// Driver: scrape every set in sets.json via the CGC JSON API and write one JSON per set.
// Exit code 0 if every set produced records, 1 otherwise.

const fs = require('fs');
const path = require('path');
const { scrapeSet } = require('./cgc-pop-scrape');

const args = process.argv.slice(2);
const flag = (k) => {
  const i = args.indexOf(k);
  return i >= 0 && args[i + 1] ? args[i + 1] : null;
};
const SETS_FILE = flag('--sets') || path.join(__dirname, 'sets.json');
const OUT_DIR = flag('--out-dir') || __dirname;
const SET_DELAY_MS = 1500;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function slugify(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

(async () => {
  const sets = JSON.parse(fs.readFileSync(SETS_FILE, 'utf8'));
  if (!Array.isArray(sets) || sets.length === 0) {
    console.error(`no sets in ${SETS_FILE}`);
    process.exit(2);
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });

  const results = [];
  let anyFail = false;

  for (const s of sets) {
    if (!s.name || !s.researchGroupID) {
      console.error('skipping malformed set entry:', s);
      anyFail = true;
      continue;
    }
    const outPath = path.join(OUT_DIR, `${slugify(s.name)}.json`);
    try {
      const summary = await scrapeSet({
        name: s.name,
        researchGroupID: s.researchGroupID,
        populationID: s.populationID || null,
        outPath,
      });
      results.push(summary);
      if (summary.total_records === 0) anyFail = true;
    } catch (e) {
      console.error(`[${s.name}] failed:`, e.message);
      results.push({
        name: s.name,
        out: outPath,
        unique_cards: 0,
        total_records: 0,
        stop_reason: `error:${e.message}`,
      });
      anyFail = true;
    }
    await sleep(SET_DELAY_MS);
  }

  const anyIncomplete = results.some((r) => r.completeness_ok === false);

  console.error('\n=== scrape summary ===');
  for (const r of results) {
    const exp = r.expected_total === null ? '?' : String(r.expected_total);
    const flag =
      r.completeness_ok === false
        ? '[INCOMPLETE]'
        : r.completeness_ok === true
        ? '[OK]'
        : '[no-total]';
    console.error(
      `  ${r.name.padEnd(28)} cards=${String(r.unique_cards).padStart(4)}/${exp.padStart(4)}  records=${String(r.total_records).padStart(5)}  stop=${r.stop_reason}  ${flag}`
    );
  }
  const totals = results.reduce(
    (a, r) => ({ cards: a.cards + r.unique_cards, records: a.records + r.total_records }),
    { cards: 0, records: 0 }
  );
  console.error(
    `total: sets=${results.length} cards=${totals.cards} records=${totals.records}` +
      (anyIncomplete ? '  (one or more sets INCOMPLETE — see above)' : '')
  );

  // Exit codes: 1 = hard failure (empty set, fetch error), 2 = completeness warning only.
  // The wrapper script treats 2 as "load the data but flag the service as failed".
  if (anyFail) process.exit(1);
  if (anyIncomplete) process.exit(2);
  process.exit(0);
})().catch((e) => {
  console.error('fatal:', e);
  process.exit(1);
});
