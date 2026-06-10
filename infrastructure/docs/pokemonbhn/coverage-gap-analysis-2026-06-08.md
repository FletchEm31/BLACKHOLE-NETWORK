# PokemonBHN — Sold Comps Coverage Gap Analysis
**Date:** 2026-06-08
**Author:** Claude Code (read-only analysis, no scraping)
**Source tables:** `sold_listings` (23,606 rows; 19,119 card_id-resolved), `master_card_catalog` (1,354 rows)
**Prior report:** `coverage-gap-analysis-2026-06-03.md`
**Purpose:** Re-audit coverage 5 days on; quantify what (if anything) changed while the operator was away.

---

## Executive Summary

1. **THE EBAY SCRAPER HAS BEEN DEAD SINCE JUNE 2 (CRITICAL / NEW).** Newest `sold_listings.inserted_at` is **2026-06-02**; **0 rows** landed since June 3. The card-by-card eBay scraper remains hard-blocked by 403 rate-limiting (per the Jun 3 handoff) and nothing has been ingested in ~6 days. **Every coverage gap below is identical to the June 3 report — none could be filled because no scraping succeeded.** This is the top action item: the eBay ingestion path is not functioning.

2. **Promos still essentially blank (CRITICAL, unchanged).** Best of Game: **0/18** variants with data. Wizards Black Star Promos: **3/53** cards (5.7%), 6 comps. The N/A-edition `buildSearchUrl` fix (commit `3bb0282`) is deployed in code but has never produced rows because the scraper can't get past eBay's WAF.

3. **Unlimited still shallow (HIGH, unchanged).** GYH/GYC Unlimited 57.6% card coverage; JGL 78.8%; TRK 81.0%. Most covered Unlimited cards remain under 3 comps — not enough for reliable pricing.

4. **231 catalog variants have zero data (MEDIUM, unchanged).** Identical to June 3. Concentrated in GYH (58), GYC (57), WBS (50), JGL (20), TRK (19), BOG (18).

5. **High-grade 1E gap persists (MEDIUM, unchanged).** PSA 10 Charizard = **1** comp; PSA 10 Blastoise & Venusaur = **0**. The card-by-card scraper doesn't reach premium high-grade listing pages; Jungle Pikachu (pulled via Chrome MCP bulk) has 112 PSA 10s by contrast.

**What changed since June 3:** essentially nothing on the eBay side. Minor differences (Shadowless 1,439→1,672 comps, WBS 3→6 comps, Fossil Unlimited depth) reflect additional `card_id` *resolution* from the title-reparse/promote work, not new scraping. Silver grew 16,184→18,069 by resolving already-ingested Bronze rows, not by adding listings.

---

## 1. Set-Level Coverage Table (2026-06-08)

| Set | Edition | Catalog | w/ Data | Coverage % | Comps | Insert Range |
|---|---|---|---|---|---|---|
| Base Set | 1st Edition | 103 | 103 | **100.0%** | 3,318 | May 21–Jun 02 |
| Base Set | Shadowless | 102 | 101 | 99.0% | 1,439 | Jun 01–Jun 02 |
| Base Set | Unlimited | 103 | 99 | 96.1% | 889 | Jun 01–Jun 02 |
| Best of Game | N/A | 11 | 0 | **0.0%** | 0 | — |
| Best of Game | Unlimited | 7 | 0 | **0.0%** | 0 | — |
| Fossil | 1st Edition | 62 | 62 | **100.0%** | 3,539 | May 21–Jun 02 |
| Fossil | N/A | 2 | 0 | **0.0%** | 0 | — |
| Fossil | Unlimited | 64 | 62 | 96.9% | 373 | Jun 01–Jun 02 |
| Gym Challenge | 1st Edition | 132 | 131 | 99.2% | 1,404 | May 21–Jun 02 |
| Gym Challenge | Unlimited | 132 | 76 | **57.6%** | 188 | May 21–Jun 02 |
| Gym Heroes | 1st Edition | 132 | 131 | 99.2% | 1,209 | May 21–Jun 02 |
| Gym Heroes | N/A | 1 | 0 | 0.0% | 0 | — |
| Gym Heroes | Unlimited | 132 | 76 | **57.6%** | 135 | Jun 01–Jun 02 |
| Jungle | 1st Edition | 64 | 64 | **100.0%** | 3,245 | May 21–Jun 02 |
| Jungle | N/A | 3 | 0 | 0.0% | 0 | — |
| Jungle | Unlimited | 80 | 63 | 78.8% | 279 | Jun 01–Jun 02 |
| Team Rocket | 1st Edition | 84 | 84 | **100.0%** | 2,817 | May 21–Jun 02 |
| Team Rocket | N/A | 3 | 0 | 0.0% | 0 | — |
| Team Rocket | Unlimited | 84 | 68 | 81.0% | 281 | Jun 01–Jun 02 |
| Wizards Black Star Promos | N/A | 53 | 3 | **5.7%** | 6 | Jun 01–Jun 02 |

**Total zero-data catalog rows: 231** (Gym Heroes 58, Gym Challenge 57, WBS 50, Jungle 20, Team Rocket 19, Best of Game 18, Base Set 5, Fossil 4).

---

## 2. Grader Coverage (comps per grader)

| Set | Edition | PSA | CGC | BGS | SGC | TAG | Note |
|---|---|---|---|---|---|---|---|
| Base Set | 1st Edition | 2,494 | 631 | 95 | 8 | 6 | PSA dominant (normal) |
| Base Set | Shadowless | 945 | 307 | 109 | 10 | 9 | Balanced top-3 |
| Base Set | Unlimited | 624 | 123 | 42 | 26 | 24 | PSA dominant |
| Fossil | 1st Edition | 2,446 | 967 | 44 | 11 | 11 | PSA/CGC OK |
| Fossil | Unlimited | 213 | 75 | 19 | 6 | 8 | Reasonable |
| Gym Challenge | 1st Edition | 988 | 326 | 50 | 4 | 10 | PSA dominant |
| Gym Challenge | Unlimited | 107 | 45 | 4 | 1 | 6 | BGS/SGC nearly absent |
| Gym Heroes | 1st Edition | 724 | 412 | 46 | 2 | 2 | BGS/SGC thin |
| Gym Heroes | Unlimited | 54 | 20 | 5 | 1 | 6 | BGS/SGC nearly absent |
| Jungle | 1st Edition | 2,441 | 697 | 34 | 12 | 8 | PSA dominant |
| Jungle | Unlimited | 211 | 30 | 3 | 2 | 1 | CGC/BGS/SGC severely thin |
| Team Rocket | 1st Edition | 1,646 | 956 | 101 | 13 | 26 | Best multi-grader coverage |
| Team Rocket | Unlimited | 137 | 43 | 18 | 11 | 11 | Reasonable |
| WBS Promos | N/A | 2 | 1 | 0 | 0 | 0 | Complete blind spot |

PSA dominance is market-driven, not scraper bias. BGS/SGC sparsity on Unlimited is largely a genuine submission gap.

---

## 3. Grade Distribution — Key Base 1E Cards (PSA)

| Card | PSA 10 | PSA 9 | PSA 8 | PSA 7 | Total | Note |
|---|---|---|---|---|---|---|
| Charizard BAS004-1E | 1 | 3 | 1 | 5 | 46 | High-grade underscraped |
| Blastoise BAS002-1E | 0 | 4 | 4 | 4 | 30 | **Missing PSA 10** |
| Venusaur BAS015-1E | 0 | 3 | 2 | 4 | 30 | **Missing PSA 10** |
| Mewtwo BAS010-1E | 3 | 2 | 5 | 3 | 34 | Thin at top |
| Pikachu JUN060-1E | 112 | 140 | 68 | 38 | 520 | Best coverage (Chrome MCP bulk) |

The most liquid cards in the hobby are missing their highest, most valuable grades — a systematic gap in the card-by-card scraper, not a market reality.

---

## 4. Shadowless & Promos Cross-Checks

- **Shadowless: NOT a blind spot.** 1,672 comps across 101/102 cards (99%). Only `BAS092-SH-STN` (Energy Removal) missing — genuinely thin market.
- **Best of Game: complete blind spot.** 0 comps, returns no rows at all.
- **Wizards Black Star Promos: near-blind spot.** 6 comps across 3 cards (50/53 missing).

---

## 5. Recommended Actions (priority order)

| # | Priority | Action | Notes |
|---|---|---|---|
| 0 | **BLOCKER** | **Fix eBay ingestion before any re-scrape.** The card-by-card scraper is 403-blocked and has produced 0 rows in 6 days. Decide path: Chrome MCP bulk (proven higher-yield — see Pikachu), residential/SOCKS proxy rotation, or a managed scrape API. Nothing else in this list is reachable until this is solved. | New top item |
| 1 | CRITICAL | Re-scrape BOG + WBS promos (N/A fix is in code) | Blocked on #0 |
| 2 | HIGH | Deepen GYH/GYC Unlimited (57.6%, most <3 comps) | Consider dropping the "unlimited" search term |
| 3 | HIGH | Deepen JGL/TRK/FSL Unlimited | Blocked on #0 |
| 4 | MEDIUM | Fill high-grade 1E gaps (Charizard/Blastoise/Venusaur PSA 10) | Chrome MCP targeted, grade-filtered |
| 5 | MEDIUM | Stand up a monthly refresh cycle for top-volume cards | No refresh cycle exists; single-window snapshot |

---

## 6. Expand to New Sets?

**No — and the case is now stronger than June 3.** Not only are 231 variants (17%) still empty within the existing 8 sets, but the ingestion path itself is non-functional. Adding a 9th set is pointless while the scraper cannot write a single new row. **Fix ingestion (#0) first; everything else follows.**
