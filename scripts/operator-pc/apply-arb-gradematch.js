#!/usr/bin/env node
/*
 * apply-arb-gradematch.js  (one-shot, 2026-06-08)
 *
 * Injects the corrected grade-matched + fee-aware arbitrage query
 * (sql/migrations/2026-06-08-arbitrage-gradematch-feemodel.sql) into the repo
 * copy of the n8n workflow JSON so the repo tracks the intended live state.
 * Does NOT touch the live n8n instance — operator pastes the query and
 * Save+Publishes in the UI.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const WF   = path.join(ROOT, 'n8n-workflows', 'pokemon', 'courtyard-bhn-arbitrage-signals.json');
const SQL  = path.join(ROOT, 'sql', 'migrations', '2026-06-08-arbitrage-gradematch-feemodel.sql');

// Pull just the executable query (drop the leading -- comment header)
const sqlRaw = fs.readFileSync(SQL, 'utf8');
const start  = sqlRaw.indexOf('WITH courtyard_asks AS (');
if (start < 0) throw new Error('could not find query start in migration file');
const query = sqlRaw.slice(start).trim();

const wf = JSON.parse(fs.readFileSync(WF, 'utf8'));
const pg = wf.nodes.find(n => (n.type || '').includes('postgres'));
if (!pg) throw new Error('postgres node not found');
pg.parameters.query = query;

// Refresh the sticky-note doc LOGIC section header so the repo doc isn't misleading
const note = wf.nodes.find(n => n.name === 'Workflow Doc');
if (note && note.parameters && typeof note.parameters.content === 'string') {
  note.parameters.content = note.parameters.content.replace(
    /LOGIC \(single Postgres Execute Query, no code nodes\)[\s\S]*?5\. INSERT into tokenized_arbitrage_signals[\s\S]*?reviewed=FALSE, actioned=FALSE, expires_at = NOW \+ 7 days\./,
`LOGIC (single Postgres Execute Query, no code nodes)  [GRADE-MATCHED + FEE-AWARE 2026-06-08]
-----------------------------------------------------
1. CTE courtyard_asks: MIN(listed_price) per (card_id, grader, numeric grade)
   for asks seen in the last 24h. Grades are GRADE-CLASS matched, not card-only.
2. CTE ebay_baseline: AVG(sold_price) per (card_id, grader, numeric grade) over
   90 days, HAVING n_comps >= 3. Numeric grade extracted via
   substring(grade FROM '[0-9]+(\\.[0-9]+)?') so Courtyard "8.5 NM-MT+" matches
   eBay "8.5". This eliminates the prior grade-blind cross-grade contamination.
3. Candidates: JOIN on (card_id, grader, grade_num), spread > 10%.
4. Fee model: CROSS JOIN LATERAL estimate_trade_costs('courtyard','ebay', ask,
   ebay_avg, 'courtyard_to_ebay') populates est_* columns + is_profitable_est.
5. Dedup per (card_id, grader, grade). INSERT with signal_strength, full est_*
   fee breakdown, reviewed=FALSE, actioned=FALSE, expires_at = NOW + 7 days.`);
}

fs.writeFileSync(WF, JSON.stringify(wf, null, 2) + '\n');
console.log('Updated', path.relative(ROOT, WF));
console.log('query length:', query.length, 'chars');
console.log('postgres node:', pg.name);
