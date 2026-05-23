#!/usr/bin/env node
/*
 * clone-vintage-workflow.js
 *
 * One-shot transform: reads n8n-workflows/pokemon/pokemon-bhn-vintage-psa.json,
 * produces pokemon-bhn-vintage-bgs.json and pokemon-bhn-vintage-sgc.json
 * with the surgical changes operator specified 2026-05-22 23:50 PT:
 *
 *   - Workflow name, id, active flag
 *   - eBay search queries: PSA -> (BGS,Beckett) / SGC
 *   - eBay search node names + notes: PSA -> BGS / SGC
 *   - GRADE FILTER regex: PSA grades -> BGS or SGC grades (with case-insensitive
 *     match for BGS so "Beckett" mixed-case passes)
 *   - Parser code (Parse & Filter Fields):
 *       * BGS detection added to the grader ternary (CGC/PSA/SGC/BGS/Raw)
 *       * Grade regex extended to include 7 and 7.5
 *       * SGC clone: also handles 1-100 scale -> normalized 1-10 grade
 *   - Disconnect Gixen Snipe: remove the edge from
 *     "Log into EventHorizon SQL" -> "Gixen Snipe" so the node is kept but
 *     never fires (operator wants this for the new graders).
 *   - Active flag set to false (operator can flip on after review).
 *   - Node UUIDs regenerated to avoid any cross-workflow collisions.
 *
 * Lives at: scripts/operator-pc/clone-vintage-workflow.js
 *
 * Usage:
 *   node scripts/operator-pc/clone-vintage-workflow.js
 *
 * Outputs:
 *   n8n-workflows/pokemon/pokemon-bhn-vintage-bgs.json
 *   n8n-workflows/pokemon/pokemon-bhn-vintage-sgc.json
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const crypto = require('crypto');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SRC = path.join(REPO_ROOT, 'n8n-workflows', 'pokemon', 'pokemon-bhn-vintage-psa.json');
const OUT_DIR = path.join(REPO_ROOT, 'n8n-workflows', 'pokemon');

function newUuid() {
    // RFC 4122 v4 - 32 hex chars in 8-4-4-4-12 with version + variant bits
    const b = crypto.randomBytes(16);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = b.toString('hex');
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}

// New parser code for the grader detection. Same shape as PSA's, with BGS
// added and the priority order configurable per grader.
function buildParserCode(primaryGrader) {
    // Always detect all four graders; prioritize the workflow's primary grader so
    // a stray non-primary token in a title (e.g. "PSA-style" in a BGS listing)
    // doesn't misclassify the row.
    const detectOrder = (primaryGrader === 'BGS')
        ? `if (t.includes('BGS') || t.includes('BECKETT')) return 'BGS';\n    if (t.includes('CGC')) return 'CGC';\n    if (t.includes('PSA')) return 'PSA';\n    if (t.includes('SGC')) return 'SGC';`
        : `if (t.includes('SGC')) return 'SGC';\n    if (t.includes('CGC')) return 'CGC';\n    if (t.includes('PSA')) return 'PSA';\n    if (t.includes('BGS') || t.includes('BECKETT')) return 'BGS';`;

    // Grade extraction. PSA used /\b(10|9\.5|9|8\.5|8)\b/. We extend the 1-10 range
    // down to 7 (BGS subgrades + SGC 7 are valid per operator spec). For SGC, we
    // ALSO try to detect the legacy 1-100 numeric scale (98=10, 96=9.5, etc.)
    // and convert to 1-10 equivalent so downstream consumers see a comparable
    // numeric grade value.
    const gradeExtraction = (primaryGrader === 'SGC')
        ? `// 1-100 scale: legacy SGC slabs. Map to 1-10 equivalent at parse time
    // so ebay_listings.grade stays consistent with master_grade_catalog vocab.
    const sgcOld = t.match(/SGC[\\s-]*(100|9[02468]|8[02468])\\b/i);
    if (sgcOld) {
      const v = parseInt(sgcOld[1]);
      if (v >= 98) return 10;
      if (v >= 96) return 9.5;
      if (v >= 92) return 9;
      if (v >= 88) return 8.5;
      if (v >= 84) return 8;
      if (v >= 80) return 7.5;
    }
    const match = t.match(/\\b(10|9\\.5|9|8\\.5|8|7\\.5|7)\\b/);
    return match ? parseFloat(match[1]) : null;`
        : `const match = t.match(/\\b(10|9\\.5|9|8\\.5|8|7\\.5|7)\\b/);
    return match ? parseFloat(match[1]) : null;`;

    return `const allItems = [];

for (const input of $input.all()) {
  const items = input.json.itemSummaries || [];
  for (const item of items) {
    allItems.push({
      json: {
        item_id: item.itemId,
        title: item.title,
        card_name: item.title,
        listed_price: parseFloat(item.price?.value || 0),
        currency: item.price?.currency ||item.currentBidPrice?.currency || 'USD',
        current_bid: parseFloat(item.currentBidPrice?.value || 0),
        bid_count: parseInt(item.bidCount || 0),
        shipping: parseFloat(item.shippingOptions?.[0]?.shippingCost?.value || 0),
        transaction_type: item.buyingOptions?.includes('AUCTION') ? 'AUCTION' : 'BUY_IT_NOW',
        obo_available: item.buyingOptions?.includes('BEST_OFFER') || false,
        obo_min_price: parseFloat(item.minimumPriceToBid?.value || 0),
        returns_accepted: item.returnTerms?.returnsAccepted || false,
        set_name: (() => {
          const t = item.title?.toUpperCase() || '';
          if (t.includes('TEAM ROCKET')) return 'Team Rocket';
          if (t.includes('GYM CHALLENGE')) return 'Gym Challenge';
          if (t.includes('GYM HEROES') || t.includes('GYM HERO')) return 'Gym Heroes';
          if (t.includes('FOSSIL')) return 'Fossil';
          if (t.includes('JUNGLE')) return 'Jungle';
          if (t.includes('BASE SET') || t.includes('BASE')) return 'Base';
          return 'Unknown';
        })(),
        grader: (() => {
          const t = item.title?.toUpperCase() || '';
          ${detectOrder}
          return 'Raw';
        })(),
        grade: (() => {
          const t = item.title || '';
          ${gradeExtraction}
        })(),
        language: (() => {
          const t = item.title?.toUpperCase() || '';
          if (t.includes('ITALIAN') || t.includes('ITALIANO')) return 'Italian';
          if (t.includes('GERMAN') || t.includes('DEUTSCH')) return 'German';
          if (t.includes('JAPANESE') || t.includes('JAPAN')) return 'Japanese';
          if (t.includes('FRENCH') || t.includes('FRANCAIS')) return 'French';
          if (t.includes('SPANISH') || t.includes('ESPANOL')) return 'Spanish';
          if (t.includes('KOREAN')) return 'Korean';
          return 'English';
        })(),
        seller_username: item.seller?.username,
        seller_feedback: item.seller?.feedbackScore,
        seller_feedback_pct: parseFloat(item.seller?.feedbackPercentage || 0),
        image_url: item.image?.imageUrl,
        item_url: item.itemHref,
        listing_url: item.itemWebUrl,
        condition: item.condition,
        item_creation_date: item.itemCreationDate || null,
        listed_at: new Date().toISOString()
      }
    });
  }
}

return allItems;`;
}

// Build the GRADE FILTER regex per grader.
//   BGS: \b(BGS|Beckett) (10|9.5|9|8.5|8|7.5|7)\b  - case-insensitive
//   SGC: \bSGC (100|98|96|94|92|90|88|86|84|82|80|10|9.5|9|8.5|8|7.5|7)\b
//        - covers both 1-10 (current) and 1-100 (legacy) scales
function buildGradeFilterRegex(primaryGrader) {
    if (primaryGrader === 'BGS') {
        return '(BGS|Beckett) (10|9\\.5|9|8\\.5|8|7\\.5|7)';
    }
    if (primaryGrader === 'SGC') {
        return 'SGC (100|98|96|94|92|90|88|86|84|82|80|10|9\\.5|9|8\\.5|8|7\\.5|7)';
    }
    throw new Error(`unknown grader: ${primaryGrader}`);
}

function cloneWorkflow(srcWf, opts) {
    // Deep-clone (preserve all metadata and unmodified nodes)
    const wf = JSON.parse(JSON.stringify(srcWf));

    // 1. Workflow header
    wf.name = `POKEMON-BHN | VINTAGE-${opts.grader}`;
    wf.id = opts.workflowId;
    wf.active = false; // operator enables manually after review

    // 2. Regenerate every node UUID for cleanliness
    for (const n of wf.nodes) {
        n.id = newUuid();
    }

    // 3. Per-node mutations
    for (const n of wf.nodes) {
        // 3a. eBay search nodes - URL + name + notes
        if (n.name && n.name.startsWith('eBay Search - PSA')) {
            n.name = n.name.replace('PSA', opts.grader);
            if (n.parameters && n.parameters.url) {
                n.parameters.url = n.parameters.url.replace(/pokemon\+PSA\+/g, `pokemon+${opts.searchToken}+`);
            }
            if (n.notes) {
                n.notes = n.notes.replace(/Grader: PSA/g, `Grader: ${opts.grader}`)
                                  .replace(/PSA/g, opts.grader);
            }
        }

        // 3b. GRADE FILTER - swap regex + make case-insensitive
        if (n.name === 'GRADE FILTER') {
            const cond = n.parameters?.conditions?.conditions?.[0];
            if (cond) {
                cond.rightValue = '=' + buildGradeFilterRegex(opts.grader);
            }
            if (n.parameters?.conditions?.options) {
                n.parameters.conditions.options.caseSensitive = false;
            }
        }

        // 3c. Parse & Filter Fields - swap the jsCode for the per-grader variant
        if (n.name === 'Parse & Filter Fields') {
            n.parameters.jsCode = buildParserCode(opts.grader);
            if (n.notes) {
                // Update the inline documentation to reflect the per-grader changes
                n.notes = n.notes.replace(/grader\s+→\s+CGC \/ PSA \/ SGC \/ Raw/g,
                                          `grader    -> CGC / PSA / SGC / BGS / Raw  (primary: ${opts.grader})`);
            }
        }
    }

    // 4. Disconnect Gixen Snipe per operator spec - keep node, remove the
    //    edge from "Log into EventHorizon SQL" to "Gixen Snipe".
    if (wf.connections && wf.connections['Log into EventHorizon SQL']) {
        wf.connections['Log into EventHorizon SQL'] = { main: [[]] };
    }

    return wf;
}

// -----------------------------------------------------------------------------
// Main
// -----------------------------------------------------------------------------

const src = JSON.parse(fs.readFileSync(SRC, 'utf8'));

const bgs = cloneWorkflow(src, {
    grader: 'BGS',
    searchToken: '(BGS,Beckett)',
    workflowId: 'bgsXCB954R4c26dN',
});

const sgc = cloneWorkflow(src, {
    grader: 'SGC',
    searchToken: 'SGC',
    workflowId: 'sgcXCB954R4c26dN',
});

const bgsPath = path.join(OUT_DIR, 'pokemon-bhn-vintage-bgs.json');
const sgcPath = path.join(OUT_DIR, 'pokemon-bhn-vintage-sgc.json');

// Write minified to match the existing PSA file format
fs.writeFileSync(bgsPath, JSON.stringify(bgs));
fs.writeFileSync(sgcPath, JSON.stringify(sgc));

// Quick smoke summary so operator can eyeball the diff
function summary(wf, label) {
    const ebaySearches = wf.nodes.filter(n => n.name.startsWith('eBay Search'));
    const gradeFilter = wf.nodes.find(n => n.name === 'GRADE FILTER');
    const parser = wf.nodes.find(n => n.name === 'Parse & Filter Fields');
    const gixenEdge = wf.connections['Log into EventHorizon SQL'];
    console.log(`--- ${label} ---`);
    console.log(`  name:          ${wf.name}`);
    console.log(`  id:            ${wf.id}`);
    console.log(`  active:        ${wf.active}`);
    console.log(`  eBay searches: ${ebaySearches.length} (first: ${ebaySearches[0].name})`);
    console.log(`  search URL:    ${ebaySearches[0].parameters.url.slice(0, 100)}...`);
    console.log(`  grade filter:  ${gradeFilter.parameters.conditions.conditions[0].rightValue.slice(0,70)}...`);
    console.log(`  parser hint:   grader detection prioritizes ${label.includes('BGS') ? 'BGS' : 'SGC'} first`);
    console.log(`  gixen edge:    ${JSON.stringify(gixenEdge.main)}  (empty == disconnected)`);
}

console.log(`Wrote ${bgsPath}`);
console.log(`Wrote ${sgcPath}`);
console.log('');
summary(bgs, 'BGS');
console.log('');
summary(sgc, 'SGC');
