# BHN SESSION HANDOFF — 2026-06-02
## For Next Claude Chat Session

---

## REPO PATH CHANGE (2026-06-02)
**OLD:** `R:\GITHUB REPOSITORY\BLACKHOLE-NETWORK`
**NEW:** `C:\GITHUB REPOSITORY\BLACKHOLE-NETWORK`
Claude Code requested this move to resolve the Proton Drive sync clobbering issue.
All references below use the new C: path.

---

## CRITICAL: READ THIS FIRST

This session made a major breakthrough on eBay scraping via Chrome MCP. The scraper
is now fully working. There are 13 JSON files in T:\Downloads ready to load into Bronze.
Claude Code also completed TAG grader onboarding and sold_date backfill — Silver is at
16,184 rows.

---

## IMMEDIATE PENDING TASKS (in order)

### 1. LOAD THE 13 SCRAPED JSON FILES (TOP PRIORITY)
Move these files from T:\Downloads → `infrastructure/scrapers/data/JSON/`

| File | Records |
|---|---|
| bhn_psa_1st_ed_high_grade_p1_240.json | 240 |
| bhn_psa_1st_ed_high_grade_p2_240.json | 240 |
| bhn_psa_1st_ed_high_grade_p3_240.json | 240 |
| bhn_psa_1st_ed_high_grade_p4_240.json | 240 |
| bhn_cgc_1st_ed_p1_240.json | 240 |
| bhn_cgc_1st_ed_p2_240.json | 240 |
| bhn_cgc_1st_ed_p3_240.json | 240 |
| bhn_cgc_1st_ed_p4_240.json | 240 |
| bhn_psa_shadowless_p1_240.json | 240 |
| bhn_psa_shadowless_p2_240.json | 240 |
| bhn_psa_shadowless_p3_240.json | 240 |
| bhn_psa_shadowless_p4_240.json | 240 |
| bhn_cgc_shadowless_p1_240.json | 240 |
| bhn_cgc_shadowless_p2_240.json | 240 |
| bhn_cgc_shadowless_p3_240.json | 240 |
| bhn_bgs_1st_ed_p1_240.json | 240 |
| bhn_bgs_1st_ed_p2_240.json | 240 |
| bhn_sgc_1st_ed_p1_54.json | 54 |
| bhn_sgc_shadowless_p1_249.json | 249 |
| bhn_bgs_shadowless_p1_229.json | 229 |

**Total: ~4,165 records across 4 graders × 2 editions**

**Load procedure:**
```bash
# On LA node:
node infrastructure/scrapers/ebay-sold-load-v8.js
node infrastructure/scrapers/ebay-title-reparse.js
# Then promote:
sudo -u postgres psql -d eventhorizon -c \
  "SELECT promoted, rejected FROM promote_bronze_to_silver();"
```

### 2. RECOVER 457 NULL sold_date ROWS
Claude Code flagged this as next after TAG. 457 Bronze rows blocked from Silver
promotion only by missing sold_date — recoverable from sold_at column.
Claude Code was about to start this when TAG task took priority.
**Status: NOT YET DONE** — Claude Code had completed TAG but was moving to this next.

### 3. n8n BRONZE_TO_SILVER_EBAY_TRANSACTIONS WORKFLOW
Briefing exists at: `infrastructure/docs/BHN session updates/bhn-bronze-to-silver-ebay.json`
Import into n8n, update credentials, Save+Publish.
Function is callable today — just needs the scheduling wrapper.

---

## POKEMONBHN PIPELINE — CURRENT STATE

### Bronze (ebay_transactions)
- ~20,588 rows before today's new files
- After loading the 13 new JSON files: expect ~24,000+ rows

### Silver (silver_ebay_transactions)
- **16,184 rows** as of end of this session
- Breakdown: PSA 11,847 · CGC 3,882 · BGS 267 · TAG 121 · SGC 67
- TAG onboarding: COMPLETE (commit a0e3777)
- sold_date backfill: COMPLETE (commit c69305a) — +447 rows promoted

### Gold (card_valuations matview)
- NOT YET BUILT — depends on Silver being fully populated
- Unblocked after loading new JSON files and running promotion

---

## SCRAPING — WORKING METHODOLOGY (CRITICAL)

### The Breakthrough
Previous scraper attempts failed because eBay changed their DOM.
**Old (broken) selector:** `ul.srp-results li.s-item` or `li.s-item`
**New (working) selector:** `ul.srp-results li.s-card`

### Working JavaScript Extractor
Run this in Claude in Chrome javascript_tool on any eBay sold listings page:

```javascript
const items=[];
for(const li of document.querySelectorAll('ul.srp-results li.s-card')){
    try{
        const text=li.innerText;
        if(!text.includes('Item:'))continue;
        const lines=text.split('\n').map(l=>l.trim()).filter(l=>l);
        const si=lines.findIndex(l=>l.startsWith('Sold '));
        if(si<0)continue;
        const sold_date=lines[si].replace('Sold ','').trim();
        const title=lines[si+1]||null;
        if(!title||title.startsWith('Opens'))continue;
        const il=lines.find(l=>l.startsWith('Item:'));
        if(!il)continue;
        const item_id=il.replace('Item:','').trim();
        const pl=lines.find(l=>/^\$[\d,]+/.test(l));
        const pm=pl?pl.match(/\$([\d,]+\.?\d*)/):null;
        const sold_price=pm?parseFloat(pm[1].replace(/,/g,'')):null;
        if(!sold_price)continue;
        const sl=lines.find(l=>/delivery|Shipping/i.test(l));
        const sm=sl?sl.match(/\$([\d,]+\.?\d*)/):null;
        const shipping=sm?parseFloat(sm[1].replace(/,/g,'')):0;
        const bl=lines.find(l=>/^\d+ bid/i.test(l));
        const bid_count=bl?parseInt(bl):0;
        let sale_type='fixed_price';
        if(bid_count>0)sale_type='auction';
        else if(/offer/i.test(text))sale_type='offer_accepted';
        items.push({item_id,title_raw:title,sold_date,sold_price,
          sale_type,bid_count,shipping,
          listing_url:'https://www.ebay.com/itm/'+item_id});
    }catch(e){}
}
// Download as JSON:
const a=document.createElement('a');
a.href=URL.createObjectURL(new Blob([JSON.stringify(items)],
  {type:'application/json'}));
a.download='bhn_[GRADER]_[TYPE]_p[N]_'+items.length+'.json';
document.body.appendChild(a);a.click();document.body.removeChild(a);
items.length+' items';
```

### eBay URL Pattern for Sold Listings
```
https://www.ebay.com/sch/i.html?
  _nkw=pokemon+[GRADER]+[EDITION/TYPE]
  &_sacat=2536
  &LH_Sold=1
  &LH_Complete=1
  &Graded=Yes
  &Language=English
  &Professional%2520Grader=[GRADER]
  &_ipg=240
  &_pgn=[PAGE_NUMBER]
```

Replace [GRADER] with: PSA, CGC, BGS, SGC
Replace [EDITION/TYPE] with: %221st+edition%22, shadowless, etc.
240 items per page. Paginate with &_pgn=1, &_pgn=2, etc.

### Important Notes
- Scroll the page before extracting (eBay lazy-loads)
- Don't use link.href directly (triggers cookie/query string block)
- Build listing_url manually: 'https://www.ebay.com/itm/'+item_id
- Files download automatically to T:\Downloads
- Move to infrastructure/scrapers/data/JSON/ before loading

### Scraping Queue (remaining)
The following searches are still untouched — continue from here:
- PSA Unlimited (all sets)
- CGC Unlimited
- BGS Unlimited  
- SGC Unlimited
- Any specific set searches (e.g. Base Set only, Fossil only)

---

## CLAUDE CODE STATUS (as of session end)

Claude Code was investigating the auto-commit mystery when session ended.
**Findings:**
- Mystery commits = GitHub Desktop (human clicking "Commit all" during parallel sessions)
- File clobbers = Proton Drive syncing R:\GITHUB REPOSITORY

**ACTION REQUIRED before next Claude Code session:**
1. Open Proton Drive settings → exclude R:\GITHUB REPOSITORY from sync
2. Don't click "Commit all" in GitHub Desktop while Claude Code is active

**Current branch:** `fix/ebay-scraper-impers-rework`
**Last commits:** c69305a (sold_date backfill), a0e3777 (TAG onboarding)

---

## EBAY LISTINGS STATUS (@fletchketchem)

12 active listings as of session end:

| Card | Price | Status |
|---|---|---|
| Dark Dragonite 22/82 SGC 9.5 | $285 OBO | Active, 7% promo |
| Psyduck Fossil 57/62 SGC 9.5 Pop 6 | $235.99 OBO | Active, 6% promo |
| Dark Kadabra 39/82 CGC 10 Chumlee | $219.99 OBO | Active, Promo pending fix |
| Dark Persian 42/82 CGC 9.5 | $178 OBO | EXPIRING ~13h from session end |
| Dark Hypno 26/82 CGC 10 | $169.99 OBO | Active |
| Dark Kadabra 39/82 CGC 9.5 | $95 OBO | Active |
| Mankey 41/82 CGC 10 | $75.99 OBO | Active |
| Dark Raticate 51/82 SGC 10 Pop 2 | $76.99 OBO | Active, new listing |
| Typhlosion PSA 9 | $74.99 OBO | Active |
| Dark Dugtrio 19/82 CGC 9.5 | $62.99 OBO | EXPIRING |
| Dark Magneton 11/82 CGC 9.5 | $62.99 OBO | EXPIRING |
| Hydreigon Confetti Holo CGC | $45 | Active |

**NOTE:** Dark Persian, Dark Raticate (not the SGC 10), Dark Dugtrio, Dark Magneton,
and GameBoy listing were expiring today — check if they need relisting.

**Outstanding listing fixes needed:**
- Dark Kadabra Chumlee: title cut off at "Chumlee Collect" (missing "ion")
- Dark Kadabra Chumlee: condition shows CGC 9.5, title says CGC 10 — needs
  explanation in description that CGC blue label 9.5 = CGC 10 Gem Mint equivalent

---

## PURCHASE COSTS (found this session)

From eBay purchase history + seller hub notes:

| Card | Cost |
|---|---|
| Dark Dragonite 22/82 SGC 9.5 | $167.87 |
| Dark Hypno 26/82 CGC 10 | $70.37 |
| Dark Kadabra 39/82 CGC 9.5 non-Chumlee | $62.63 |
| Dark Persian CGC 9.5 | $26.55 |
| Dark Raticate SGC 10 Pop 2 | $23.24 |
| Dark Dugtrio CGC 9.5 | $43.10 |
| Dark Magneton CGC 9.5 | $32.31 |
| Typhlosion PSA 9 | $32.50 |

**Still unknown (not found in eBay history):**
- Psyduck Fossil SGC 9.5 Pop 6
- Dark Kadabra Chumlee CGC 10
- Mankey CGC 10

---

## CARDS AT GAMESTOP (LOST — URGENT)

Two PSA 8 cards were submitted to GameStop and are currently lost:
- Sabrina's Gastly 96/132 Gym Challenge 1st Edition — PSA 8 — Cert 154271366
- Sabrina's Psyduck 99/132 Gym Challenge 1st Edition — PSA 8 — Cert 154271367

**Market values:**
- Gastly PSA 8: ~$25
- Psyduck PSA 8: ~$62 (PSA 9: $69-85, PSA 10: **$590**)

**Action:** File formal written claim with GameStop. When found — DO NOT LIST AT PSA 8.
Resubmit both to PSA first (strong regrade candidates — photos look clean).

---

## KEY TECHNICAL REMINDERS

- NJ SSH always port 2222
- PostgreSQL: `sudo -u postgres psql -d eventhorizon` (no -h flag)
- Silver is fully derived from Bronze — TRUNCATE + re-run is always safe
- TAG grader: now formally onboarded, CGC 9.5 blue label = CGC 10 Gem Mint equivalent
- CGC 9.5 blue label = CGC 10 Gem Mint (official CGC policy since July 2023)
- GitHub commits: Summary AND Description both required

---

*Session ended: 2026-06-02 | Branch: fix/ebay-scraper-impers-rework*
