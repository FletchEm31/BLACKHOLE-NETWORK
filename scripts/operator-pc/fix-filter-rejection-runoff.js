#!/usr/bin/env node
/*
 * fix-filter-rejection-runoff.js
 *
 * Bug: all 8 POKEMON-BHN | VINTAGE-* workflows' "FILTER REJECTION RUNOFF"
 * Postgres node uses mappingMode=autoMapInputData against table
 * filter_rejections. At runtime an upstream eBay error/warning payload can
 * carry a `message` field, which auto-map then tries to insert as a column
 * that does not exist in filter_rejections -> "column message does not exist".
 *
 * Fix (operator-approved 2026-05-29): convert the node from autoMapInputData
 * to defineBelow, explicitly mapping only the 25 parsed fields that have
 * matching columns in filter_rejections. Any stray field (message, or
 * anything else an error item injects) is simply not mapped, so the insert
 * no longer breaks.
 *
 * Column set verified against the live filter_rejections schema (38 cols) on
 * LA (10.8.0.1) eventhorizon 2026-05-29. Columns left unmapped (id, created_at,
 * rejected_at, filter_stage, and the enriched-observation cols) keep their
 * prior behaviour: they were never set by the parser under auto-map either.
 *
 * Idempotent: skips any node already on defineBelow.
 *
 * Usage:  node scripts/operator-pc/fix-filter-rejection-runoff.js
 * Source of truth only -- does NOT deploy to the live n8n instance.
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const POKEMON_DIR = path.resolve(__dirname, '..', '..', 'n8n-workflows', 'pokemon');
const NODE_NAME   = 'FILTER REJECTION RUNOFF';

// [column, n8n resourceMapper type] -- parsed fields that map 1:1 to
// filter_rejections columns. Order mirrors the Parse & Filter Fields output.
const COLS = [
  ['item_id',             'string'],
  ['title',               'string'],
  ['card_name',           'string'],
  ['listed_price',        'number'],
  ['currency',            'string'],
  ['current_bid',         'number'],
  ['bid_count',           'number'],
  ['shipping',            'number'],
  ['transaction_type',    'string'],
  ['obo_available',       'boolean'],
  ['obo_min_price',       'number'],
  ['returns_accepted',    'boolean'],
  ['set_name',            'string'],
  ['grader',              'string'],
  ['grade',               'number'],
  ['language',            'string'],
  ['seller_username',     'string'],
  ['seller_feedback',     'number'],
  ['seller_feedback_pct', 'number'],
  ['image_url',           'string'],
  ['item_url',            'string'],
  ['listing_url',         'string'],
  ['condition',           'string'],
  ['item_creation_date',  'dateTime'],
  ['listed_at',           'dateTime'],
];

function buildColumns() {
  const value = {};
  for (const [c] of COLS) value[c] = `={{ $json.${c} }}`;

  const schema = [
    // id present in schema (serial PK) but NOT in value -> uses DB default.
    { id: 'id', displayName: 'id', required: false, defaultMatch: true,
      display: true, type: 'number', canBeUsedToMatch: true, removed: false },
    ...COLS.map(([c, t]) => ({
      id: c, displayName: c, required: false, defaultMatch: false,
      display: true, type: t, canBeUsedToMatch: false,
    })),
  ];

  return {
    mappingMode: 'defineBelow',
    value,
    matchingColumns: [],
    schema,
    attemptToConvertTypes: false,
    convertFieldsToString: false,
  };
}

let changed = 0, skipped = 0;
for (const file of fs.readdirSync(POKEMON_DIR).filter(f => /^pokemon-bhn-vintage-.*\.json$/.test(f))) {
  const fp = path.join(POKEMON_DIR, file);
  const wf = JSON.parse(fs.readFileSync(fp, 'utf8'));
  const node = (wf.nodes || []).find(n => n.name === NODE_NAME);
  if (!node) { console.log(`-- ${file}: no ${NODE_NAME} node, skipped`); skipped++; continue; }

  if (node.parameters.columns && node.parameters.columns.mappingMode === 'defineBelow') {
    console.log(`== ${file}: already defineBelow, skipped`); skipped++; continue;
  }

  node.parameters.operation = 'insert';   // make explicit (was default)
  node.parameters.columns   = buildColumns();

  fs.writeFileSync(fp, JSON.stringify(wf));  // keep minified single-line form
  console.log(`++ ${file}: FILTER REJECTION RUNOFF -> defineBelow (${COLS.length} cols, message dropped)`);
  changed++;
}

console.log(`\nDone. changed=${changed} skipped=${skipped}`);
