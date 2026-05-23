#!/usr/bin/env node
/*
 * add-shadowless-and-unlimited.js
 *
 * Two transforms, applied to all 4 vintage 1st-Edition workflows
 * (PSA/CGC/BGS/SGC):
 *
 *   1. SHADOWLESS — add a "Base Set Shadowless" eBay search node to each
 *      original workflow, wired into the existing Merge at the next free
 *      input index. Source files are rewritten in place. (Shadowless is a
 *      Base Set–only print per the collectibles data standard §3.3.)
 *
 *   2. UNLIMITED — produce a sibling workflow targeting Unlimited edition,
 *      with belt-and-suspenders exclusion of 1st Edition + Shadowless:
 *        - eBay search queries replace `"1st+edition"` with `"unlimited"`
 *          and add negative tokens `-1st -shadowless` in `q`.
 *        - A new EDITION FILTER node sits between
 *          "Insert or update rows in a table" and "LANGUAGE FILTER",
 *          rejecting any title containing "1st" or "shadowless" (case-
 *          insensitive). False branch goes to FILTER REJECTION RUNOFF.
 *        - Promo searches (Wizards Black Star Promos, Best of Game Promos)
 *          carry no edition qualifier; kept as-is so the workflow still
 *          covers promos.
 *        - The Shadowless node added by transform #1 is removed from the
 *          Unlimited clone.
 *        - Gixen Snipe edge from "Log into EventHorizon SQL" is cleared
 *          (node retained, operator can re-enable later).
 *        - active = false; cron = `0 0,30 * * * *` (operator-selected
 *          stagger).
 *        - All node UUIDs regenerated. New workflow id.
 *
 * Outputs land in n8n-workflows/pokemon/:
 *   pokemon-bhn-vintage-{grader}.json         (rewritten — Shadowless added)
 *   pokemon-bhn-vintage-{grader}-unlimited.json   (new)
 *
 * Usage:
 *   node scripts/operator-pc/add-shadowless-and-unlimited.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const crypto = require('crypto');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const POKEMON_DIR = path.join(REPO_ROOT, 'n8n-workflows', 'pokemon');

// ---------------------------------------------------------------------------
// Per-grader config
// ---------------------------------------------------------------------------

const GRADERS = [
    { code: 'PSA', searchToken: 'PSA',            file: 'pokemon-bhn-vintage-psa.json' },
    { code: 'CGC', searchToken: 'CGC',            file: 'pokemon-bhn-vintage-cgc.json' },
    { code: 'BGS', searchToken: '(BGS,Beckett)',  file: 'pokemon-bhn-vintage-bgs.json' },
    { code: 'SGC', searchToken: 'SGC',            file: 'pokemon-bhn-vintage-sgc.json' },
];

function newUuid() {
    const b = crypto.randomBytes(16);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = b.toString('hex');
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}

function newWorkflowId() {
    // n8n workflow ids are opaque 16-char alphanumeric. Match that shape.
    return crypto.randomBytes(12).toString('base64')
        .replace(/\+/g, 'a').replace(/\//g, 'b').replace(/=/g, '').slice(0, 16);
}

// ---------------------------------------------------------------------------
// Transform 1 — Shadowless
// ---------------------------------------------------------------------------

function buildShadowlessNode(grader, searchToken) {
    return {
        parameters: {
            url: `https://api.ebay.com/buy/browse/v1/item_summary/search?q=pokemon+${searchToken}+"shadowless"+"Base+Set"&category_ids=2536&sort=newlyListed&limit=100&filter=buyingOptions:%7BAUCTION%7D`,
            sendHeaders: true,
            headerParameters: {
                parameters: [
                    { name: 'Authorization', value: "=Bearer {{ $('Extract Bearer Token').item.json.bearer_token }}" }
                ]
            },
            options: {}
        },
        type: 'n8n-nodes-base.httpRequest',
        typeVersion: 4.4,
        position: [-200, 1024],
        id: newUuid(),
        name: `eBay Search - ${grader} Base Set Shadowless`,
        alwaysOutputData: true,
        onError: 'continueRegularOutput',
        notes: `Grader: ${grader}\nEdition: "shadowless" (exact phrase)\nSet: "base set" (exact phrase)\nListing type: Auctions only\nSort: Newly listed\nLimit: 100`
    };
}

function addShadowless(wf, grader, searchToken) {
    const mergeNode = wf.nodes.find(n => n.name === 'Merge');
    if (!mergeNode) throw new Error(`${grader}: Merge node missing`);

    const idempotentName = `eBay Search - ${grader} Base Set Shadowless`;
    if (wf.nodes.some(n => n.name === idempotentName)) {
        // Already added on a prior run — skip.
        return { added: false };
    }

    const currentInputs = mergeNode.parameters.numberInputs;
    const newInputIndex = currentInputs;
    mergeNode.parameters.numberInputs = currentInputs + 1;

    const shadowlessNode = buildShadowlessNode(grader, searchToken);
    wf.nodes.push(shadowlessNode);

    // Wire Extract Bearer Token → new node
    const ebtConn = wf.connections['Extract Bearer Token'];
    if (!ebtConn) throw new Error(`${grader}: Extract Bearer Token connection missing`);
    ebtConn.main[0].push({ node: idempotentName, type: 'main', index: 0 });

    // Wire new node → Merge at next index
    wf.connections[idempotentName] = {
        main: [[{ node: 'Merge', type: 'main', index: newInputIndex }]]
    };

    return { added: true, mergeInputs: currentInputs + 1 };
}

// ---------------------------------------------------------------------------
// Transform 2 — Unlimited
// ---------------------------------------------------------------------------

function transformSearchUrlToUnlimited(url) {
    // Replace `"1st+edition"+` with `"unlimited"+`, and append negative tokens.
    // Promo searches that lack `"1st+edition"` are left alone (no edition).
    if (!url.includes('"1st+edition"')) return url;
    let next = url.replace(/"1st\+edition"/g, '"unlimited"');
    // Append negative tokens immediately after the q= terms.
    // q=...&category_ids=... → insert `+-1st+-shadowless` before the first &.
    next = next.replace(/(q=[^&]+)/, '$1+-1st+-shadowless');
    return next;
}

function buildEditionFilterNode(parseFilterPos) {
    // Park EDITION FILTER above LANGUAGE FILTER on the canvas.
    const [px, py] = parseFilterPos || [800, 100];
    return {
        parameters: {
            conditions: {
                options: { caseSensitive: false, leftValue: '', typeValidation: 'strict', version: 3 },
                conditions: [
                    {
                        id: newUuid(),
                        leftValue: '={{ $json.title }}',
                        rightValue: '1st',
                        operator: { type: 'string', operation: 'notContains' }
                    },
                    {
                        id: newUuid(),
                        leftValue: '={{ $json.title }}',
                        rightValue: 'shadowless',
                        operator: { type: 'string', operation: 'notContains' }
                    }
                ],
                combinator: 'and'
            },
            options: { ignoreCase: true }
        },
        type: 'n8n-nodes-base.if',
        typeVersion: 2.3,
        position: [px + 80, py + 160],
        id: newUuid(),
        name: 'EDITION FILTER',
        alwaysOutputData: true,
        onError: 'continueRegularOutput',
        notes: 'EDITION FILTER (Unlimited workflow)\n----------------------------------\nRejects any listing whose title contains "1st" or "shadowless".\nBelt-and-suspenders to the eBay -1st -shadowless negative tokens in the search query.\nFalse → FILTER REJECTION RUNOFF (filter_rejections).'
    };
}

function buildUnlimited(originalWf, grader, searchToken) {
    // Deep clone, then strip any Shadowless search node + its wiring so the
    // Unlimited sibling never carries a shadowless fetch (we exclude
    // shadowless via negative tokens + EDITION FILTER instead).
    const wf = JSON.parse(JSON.stringify(originalWf));

    const shadowlessName = `eBay Search - ${grader} Base Set Shadowless`;
    const slNode = wf.nodes.find(n => n.name === shadowlessName);
    if (slNode) {
        // Drop the node
        wf.nodes = wf.nodes.filter(n => n.name !== shadowlessName);
        // Drop its outbound connection key
        if (wf.connections[shadowlessName]) delete wf.connections[shadowlessName];
        // Drop the Extract Bearer Token edge that points at it; the Merge
        // input that this fed is left as an unused (empty) index, matching
        // the pre-shadowless shape (Merge.numberInputs stays bumped, but
        // that's harmless — Merge with a missing input simply has fewer
        // upstream items).
        const ebt = wf.connections['Extract Bearer Token'];
        if (ebt && ebt.main && ebt.main[0]) {
            ebt.main[0] = ebt.main[0].filter(e => e.node !== shadowlessName);
        }
        // Roll back the Merge input bump so the canvas matches the
        // pre-shadowless shape exactly.
        const merge = wf.nodes.find(n => n.name === 'Merge');
        if (merge && merge.parameters.numberInputs > 0) {
            merge.parameters.numberInputs -= 1;
        }
    }

    wf.id = newWorkflowId();
    wf.name = `POKEMON-BHN | VINTAGE-${grader}-UNLIMITED`;
    wf.active = false;
    wf.versionId = newUuid();
    wf.activeVersionId = newUuid();
    wf.versionCounter = 1;

    // Cron — operator-selected stagger.  NOTE: collides with CGC original
    // (which runs :00 :30).  Workflows ship inactive so operator can
    // adjust before flipping on.
    const sched = wf.nodes.find(n => n.name === 'Schedule Trigger');
    if (sched) sched.parameters.rule.interval[0].expression = '0 0,30 * * * *';

    // Regenerate every node UUID
    for (const n of wf.nodes) n.id = newUuid();

    // Build rename map by walking every node (not just connection keys —
    // some nodes lack outbound edges in the live workflows yet are still
    // referenced as targets from Extract Bearer Token).
    const renameNodeName = (name) => name
        .replace(/\b1st Edition V2\b/g, 'Unlimited V2')
        .replace(/\b1st Edition1\b/g, 'Unlimited')
        .replace(/\b1st Edition\b/g, 'Unlimited');

    const renameMap = {};
    for (const n of wf.nodes) {
        if (!n.name || !n.name.startsWith('eBay Search')) continue;
        const renamed = renameNodeName(n.name);
        if (renamed !== n.name) renameMap[n.name] = renamed;

        n.name = renamed;
        if (n.parameters && n.parameters.url) {
            n.parameters.url = transformSearchUrlToUnlimited(n.parameters.url);
        }
        if (n.notes) {
            n.notes = n.notes
                .replace(/Edition: "1st edition" \(exact phrase\)/g, 'Edition: "unlimited" (exact phrase)')
                .replace(/"1st edition"/g, '"unlimited"');
        }
    }

    // Rename connection KEYS (outbound-edge sources)
    for (const [oldName, newName] of Object.entries(renameMap)) {
        if (wf.connections[oldName]) {
            wf.connections[newName] = wf.connections[oldName];
            delete wf.connections[oldName];
        }
    }
    // Update right-hand-side node refs in every connection branch
    for (const conn of Object.values(wf.connections)) {
        for (const branch of conn.main || []) {
            for (const edge of branch) {
                if (renameMap[edge.node]) edge.node = renameMap[edge.node];
            }
        }
    }

    // Insert EDITION FILTER between "Insert or update rows in a table" and "LANGUAGE FILTER"
    const parseFilter = wf.nodes.find(n => n.name === 'Parse & Filter Fields');
    const editionFilter = buildEditionFilterNode(parseFilter && parseFilter.position);
    wf.nodes.push(editionFilter);

    // Rewire: was Insert→LANGUAGE, now Insert→EDITION→LANGUAGE
    const insertConn = wf.connections['Insert or update rows in a table'];
    if (insertConn) {
        insertConn.main = [[{ node: 'EDITION FILTER', type: 'main', index: 0 }]];
    }
    wf.connections['EDITION FILTER'] = {
        main: [
            [{ node: 'LANGUAGE FILTER', type: 'main', index: 0 }],
            [{ node: 'FILTER REJECTION RUNOFF', type: 'main', index: 0 }]
        ]
    };

    // Disconnect Gixen Snipe (keep node, drop edge)
    if (wf.connections['Log into EventHorizon SQL']) {
        wf.connections['Log into EventHorizon SQL'] = { main: [[]] };
    }

    return wf;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function summary(wf, label) {
    const ebaySearches = wf.nodes.filter(n => n.name.startsWith('eBay Search'));
    const merge = wf.nodes.find(n => n.name === 'Merge');
    const editionFilter = wf.nodes.find(n => n.name === 'EDITION FILTER');
    const gixenEdge = wf.connections['Log into EventHorizon SQL'];
    const sched = wf.nodes.find(n => n.name === 'Schedule Trigger');
    console.log(`--- ${label} ---`);
    console.log(`  name:           ${wf.name}`);
    console.log(`  id:             ${wf.id}`);
    console.log(`  active:         ${wf.active}`);
    console.log(`  cron:           ${sched ? sched.parameters.rule.interval[0].expression : '(no schedule)'}`);
    console.log(`  eBay searches:  ${ebaySearches.length}`);
    console.log(`  Merge inputs:   ${merge.parameters.numberInputs}`);
    console.log(`  EDITION FILTER: ${editionFilter ? 'present' : 'absent'}`);
    console.log(`  Gixen edge:     ${gixenEdge ? JSON.stringify(gixenEdge.main) : '(no Log node)'}`);
}

for (const { code, searchToken, file } of GRADERS) {
    const srcPath = path.join(POKEMON_DIR, file);
    const original = JSON.parse(fs.readFileSync(srcPath, 'utf8'));

    // Snapshot BEFORE adding shadowless — used as Unlimited base
    const beforeShadowless = JSON.parse(JSON.stringify(original));

    // Transform 1: add Shadowless and write back
    const result = addShadowless(original, code, searchToken);
    fs.writeFileSync(srcPath, JSON.stringify(original));
    console.log(`Updated ${srcPath} — Shadowless ${result.added ? 'added' : 'already present'} (Merge inputs now ${original.nodes.find(n=>n.name==='Merge').parameters.numberInputs})`);

    // Transform 2: build Unlimited sibling
    const unlimited = buildUnlimited(beforeShadowless, code, searchToken);
    const unlPath = path.join(POKEMON_DIR, file.replace('.json', '-unlimited.json'));
    fs.writeFileSync(unlPath, JSON.stringify(unlimited));
    console.log(`Wrote   ${unlPath}`);

    summary(unlimited, `${code}-UNLIMITED`);
    console.log('');
}

console.log('Done.  Reminder: all 4 *-unlimited.json workflows ship with active=false.');
console.log('Cron `0 0,30` will collide with the live CGC 1st Edition workflow (also :00 :30).');
console.log('Operator should adjust before activating CGC-UNLIMITED.');
