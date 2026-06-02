#!/usr/bin/env node
// ebay-title-reparse.js — recover card identity on ebay_transactions (Bronze).
//
// Two passes, both idempotent and safe to re-run:
//   1) BACKFILL: re-parse title_raw → fill NULL set_name / card_number / edition / grader / grade
//      (NULL-only — never overwrites loader-provided values; edition only when the title states it).
//   2) RESOLVE : UPDATE card_id = resolve_card_id(card_name,set_name,card_number,edition,print_variant)
//      for rows where card_id IS NULL (the deployed STABLE fn; correct '/denominator' normalization).
//
// This unblocks Silver promotion (gate needs card_id + edition NOT NULL) and recovers the
// "unmatched" rows. The grader+grade title parser is copied verbatim from ebay-sold-scrape.js;
// set/number/edition/variant parsers match ebay-sold-load batch transforms.
//
// Usage (run on LA; socket peer-auth as postgres):
//   sudo -u postgres node ebay-title-reparse.js --dry-run     # report only, no writes
//   sudo -u postgres node ebay-title-reparse.js --apply       # backfill + resolve
//   flags: --host (/var/run/postgresql) --db (eventhorizon) --user (postgres) --limit N
'use strict';
const { Client } = require('pg');

const argv = process.argv.slice(2);
const flag = (k, d = null) => { const i = argv.indexOf(k); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };
const DRY_RUN = argv.includes('--dry-run') || !argv.includes('--apply');
const PG_HOST = flag('--host', process.env.PGHOST || '/var/run/postgresql');
const PG_DB   = flag('--db',   process.env.PGDATABASE || 'eventhorizon');
const PG_USER = flag('--user', process.env.PGUSER || 'postgres');
const PG_PORT = parseInt(flag('--port', process.env.PGPORT || '5432'), 10);
const LIMIT   = flag('--limit', null);

// ── grader+grade parser (verbatim from ebay-sold-scrape.js:112-234) ───────────
const CGC_TIER_LABELS = ['Perfect', 'Pristine', 'Gem Mint', 'Mint+', 'Mint', 'Near Mint-Mint+',
  'Near Mint-Mint', 'Near Mint+', 'Near Mint', 'Excellent-Mint+', 'Excellent-Mint',
  'Excellent+', 'Excellent', 'Very Good-Excellent+', 'Very Good-Excellent',
  'Very Good+', 'Very Good', 'Good+', 'Good', 'Fair', 'Poor'];
const BGS_SGC_TIER_LABELS = ['Pristine', 'Gem Mint', 'Mint+', 'Mint',
  'Near Mint-Mint+', 'Near Mint-Mint', 'Near Mint+', 'Near Mint',
  'Excellent-Mint+', 'Excellent-Mint', 'Excellent+', 'Excellent',
  'Very Good-Excellent+', 'Very Good-Excellent', 'Very Good+', 'Very Good',
  'Good+', 'Good', 'Fair', 'Poor', 'Authentic'];
function parseGradeFromTitle(title) {
  if (!title) return { grader: null, grade: null };
  const t = title.toUpperCase();
  let grader = null;
  if (t.includes('PSA')) grader = 'PSA';
  else if (t.includes('CGC')) grader = 'CGC';
  else if (t.includes('BGS')) grader = 'BGS';
  else if (t.includes('SGC')) grader = 'SGC';
  if (!grader) return { grader: null, grade: null };
  if (grader === 'PSA') {
    const m = title.match(/\bPSA\s+(\d+\.?\d*)\b/i);
    if (m) return { grader, grade: m[1] };
    const m2 = title.match(/\bPSA\b.*?(\d+\.?\d*)\s*(?:$|[^/\d])/i);
    if (m2) return { grader, grade: m2[1] };
    return { grader, grade: null };
  }
  if (grader === 'CGC') {
    for (const tier of CGC_TIER_LABELS) {
      const m = title.match(new RegExp(`\\b${tier}\\s+(\\d+\\.?\\d*)\\b`, 'i'));
      if (m) { const num = m[1]; return { grader, grade: parseFloat(num) < 10 ? num : `${tier} ${num}` }; }
    }
    const m = title.match(/\bCGC\s+(\d+\.?\d*)\b/i);
    if (m) { const num = m[1]; if (num === '10') return { grader, grade: null }; return { grader, grade: num }; }
    return { grader, grade: null };
  }
  for (const tier of BGS_SGC_TIER_LABELS) {
    const m = title.match(new RegExp(`\\b${tier}\\s+(\\d+\\.?\\d*)\\b`, 'i'));
    if (m) return { grader, grade: `${tier} ${m[1]}` };
    const m2 = title.match(new RegExp(`\\b${grader}\\s+(\\d+\\.?\\d*)\\s+${tier}\\b`, 'i'));
    if (m2) return { grader, grade: `${tier} ${m2[1]}` };
  }
  const fb = title.match(new RegExp(`\\b${grader}\\s+(\\d+\\.?\\d*)\\b`, 'i'));
  if (fb) return { grader, grade: fb[1] };
  return { grader, grade: null };
}
function gradeForBgsSgc(grader, grade) {
  if ((grader === 'BGS' || grader === 'SGC') && grade) {
    const mm = grade.match(/(\d+\.?\d*)\s*$/);
    if (mm && parseFloat(mm[1]) < 10) return mm[1]; // live BGS/SGC catalog uses numeric <10
  }
  return grade;
}
function parseSet(t) {
  const u = (t || '').toUpperCase();
  if (/GYM\s+HEROES/.test(u)) return 'Gym Heroes';
  if (/GYM\s+CHALLENGE/.test(u)) return 'Gym Challenge';
  if (/TEAM\s+ROCKET/.test(u)) return 'Team Rocket';
  if (/\bFOSSIL\b/.test(u)) return 'Fossil';
  if (/\bJUNGLE\b/.test(u)) return 'Jungle';
  if (/BEST\s+OF\s+GAME/.test(u)) return 'Best of Game';
  if (/BLACK\s*STAR|WOTC\s+PROMO/.test(u)) return 'Wizards Black Star Promos';
  if (/BASE\s+SET|SHADOWLESS/.test(u)) return 'Base Set';
  return null;
}
function parseCardNumber(t) {
  let m = (t || '').match(/#?\s*(\d{1,3})\s*\/\s*\d{1,3}/);
  if (m) return m[1];
  m = (t || '').match(/#\s*(\d{1,3})\b/);
  if (m) return m[1];
  m = (t || '').match(/\bNo\.?\s*(\d{1,3})\b/i);
  if (m) return m[1];
  return null;
}
// Explicit-only: returns null unless the title actually states the edition (so we never
// backfill a guessed 'Unlimited' — resolve_card_id() handles NULL edition via its attempt-2).
function parseEditionExplicit(t) {
  const u = (t || '').toUpperCase();
  if (/1ST\s*ED(ITION)?|FIRST\s+EDITION/.test(u)) return '1st Edition';
  if (/SHADOWLESS/.test(u)) return 'Shadowless';
  if (/UNLIMITED/.test(u)) return 'Unlimited';
  return null;
}

async function main() {
  const client = new Client({ host: PG_HOST, database: PG_DB, user: PG_USER, port: PG_PORT, password: process.env.PGPASSWORD });
  await client.connect();
  console.log(`Connected to ${PG_DB} as ${PG_USER} via ${PG_HOST}.${DRY_RUN ? '  [DRY-RUN]' : '  [APPLY]'}`);

  const before = (await client.query(
    `SELECT COUNT(*) t, COUNT(card_id) c, COUNT(set_name) s, COUNT(card_number) n,
            COUNT(edition) e, COUNT(grader) g, COUNT(grade) gr FROM ebay_transactions`)).rows[0];
  console.log(`Before: ${before.t} rows | card_id ${before.c} | set ${before.s} | num ${before.n} | edition ${before.e} | grader ${before.g} | grade ${before.gr}`);

  // Live (grader, raw_label) pairs — used to reject mis-parsed grades (e.g. a year captured as a grade)
  // before they hit the ebay_transactions (grader, grade) FK.
  const validGrades = new Set((await client.query('SELECT grader, raw_label FROM master_grade_catalog')).rows.map((x) => `${x.grader}|${x.raw_label}`));

  const sql = `SELECT id, title_raw, set_name, card_number, edition, grader, grade
               FROM ebay_transactions WHERE title_raw IS NOT NULL
               AND (set_name IS NULL OR card_number IS NULL OR edition IS NULL OR grader IS NULL OR grade IS NULL)
               ${LIMIT ? `LIMIT ${parseInt(LIMIT, 10)}` : ''}`;
  const rows = (await client.query(sql)).rows;
  console.log(`Candidate rows needing backfill (a NULL component + a title): ${rows.length}`);

  const fill = { set_name: 0, card_number: 0, edition: 0, grader: 0, grade: 0 };
  let gradeDropped = 0;
  const updates = []; // {id, cols:{...}}
  for (const r of rows) {
    const g = parseGradeFromTitle(r.title_raw);
    const effGrader = r.grader || g.grader;  // grade FK validates against existing OR backfilled grader
    let candGrade = r.grade ? null : gradeForBgsSgc(g.grader, g.grade);
    // Drop mis-parses (e.g. a year "1999" captured as a PSA grade): only keep grades the
    // (effective grader, grade) FK will accept.
    if (candGrade && (!effGrader || !validGrades.has(`${effGrader}|${candGrade}`))) { candGrade = null; gradeDropped++; }
    const cand = {
      set_name:    r.set_name    ? null : parseSet(r.title_raw),
      card_number: r.card_number ? null : parseCardNumber(r.title_raw),
      edition:     r.edition     ? null : parseEditionExplicit(r.title_raw),
      grader:      r.grader      ? null : g.grader,
      grade:       candGrade,
    };
    const set = {};
    for (const k of Object.keys(cand)) if (cand[k] != null) { set[k] = cand[k]; fill[k]++; }
    if (Object.keys(set).length) updates.push({ id: r.id, set });
  }
  console.log(`Backfill plan: set_name +${fill.set_name}, card_number +${fill.card_number}, edition +${fill.edition}, grader +${fill.grader}, grade +${fill.grade}  (rows touched: ${updates.length}; mis-parsed grades dropped: ${gradeDropped})`);

  // Projected card_id resolution (read-only; runs the real fn against CURRENT fields as a floor).
  const wouldNow = (await client.query(
    `SELECT COUNT(*) c FROM ebay_transactions WHERE card_id IS NULL
       AND resolve_card_id(card_name, set_name, card_number, edition, print_variant) IS NOT NULL`)).rows[0].c;
  console.log(`card_id resolvable NOW (pre-backfill, currently NULL): +${wouldNow}  → floor total ${Number(before.c) + Number(wouldNow)} / ${before.t}`);

  if (DRY_RUN) {
    console.log('\n[DRY-RUN] no writes. Backfill would add the components above, then resolve_card_id()');
    console.log('[DRY-RUN] would resolve at least the floor shown (more after set/num backfill).');
    await client.end();
    return;
  }

  // APPLY — pass 1: backfill (batched). Grades were already FK-validated during candidate build.
  let applied = 0;
  await client.query('BEGIN');
  for (const u of updates) {
    const cols = Object.keys(u.set);
    if (!cols.length) continue;
    const sets = cols.map((c, i) => `${c} = $${i + 2}`).join(', ');
    await client.query(`UPDATE ebay_transactions SET ${sets} WHERE id = $1`, [u.id, ...cols.map(c => u.set[c])]);
    applied++;
  }
  // APPLY — pass 2: resolve card_id for all still-NULL rows using the deployed function.
  const res = await client.query(
    `UPDATE ebay_transactions SET card_id =
       resolve_card_id(card_name, set_name, card_number, edition, print_variant)
     WHERE card_id IS NULL
       AND resolve_card_id(card_name, set_name, card_number, edition, print_variant) IS NOT NULL`);
  await client.query('COMMIT');

  const after = (await client.query(
    `SELECT COUNT(*) t, COUNT(card_id) c, COUNT(set_name) s, COUNT(card_number) n,
            COUNT(edition) e, COUNT(grader) g, COUNT(grade) gr FROM ebay_transactions`)).rows[0];
  console.log(`\nApplied: ${applied} rows backfilled (mis-parsed grades dropped: ${gradeDropped}); card_id resolved this run: ${res.rowCount}`);
  console.log(`After:  card_id ${after.c} (${(after.c / after.t * 100).toFixed(1)}%) | set ${after.s} | num ${after.n} | edition ${after.e} | grader ${after.g} | grade ${after.gr}`);
  await client.end();
}
main().catch((e) => { console.error('fatal:', e); process.exit(1); });
