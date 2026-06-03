# PokemonBHN — Sold Comps Coverage Gap Analysis
**Date:** 2026-06-03  
**Author:** Claude Code (read-only analysis, no scraping)  
**Source tables:** `sold_listings` / `ebay_transactions` (23,606 rows), `master_card_catalog` (1,354 rows)  
**Purpose:** Identify systematic blind spots before deciding whether to expand to new sets.

---

## Executive Summary

1. **Promos are almost entirely uncovered (CRITICAL).** Best of Game: 0/18 variants with data. Wizards Black Star Promos: 3/53 cards with data (5.7%). Root cause: the N/A edition bug in `buildSearchUrl` was live for the full scraping run — BOG and WBS searched `pokemon Best of Game 1st edition ...` instead of dropping the edition term. Promo re-scrape is the single highest-priority fill before expanding sets.

2. **Unlimited is shallow across all sets (HIGH).** Coverage ranges from 57.6% (GYH/GYC) to 96.9% (Fossil) at the card level, but comp *depth* is the bigger problem. GYH Unlimited: 64/76 covered cards have fewer than 3 comps — effectively unusable for pricing. GYC Unlimited: 51/76 under 3 comps. The "unlimited" search term + 403 rate-limiting during the first pass left data thin.

3. **231 catalog variants have zero data (MEDIUM).** Most are Unlimited low-end cards and promo variants. 16 of these are Unlimited TRK/BST/JGL/FSL cards that should have liquid markets. The rest are thin-market energy cards, error variants, and promos that may genuinely have sparse eBay history.

4. **1E coverage is strong but not complete (LOW).** Two 1st Edition cards have no data: `GYC111-1E-STN` (Blaine's Quiz #2) and `GYH113-1E-STN` (Minion of Team Rocket) — likely thin-market trainer cards. Everything else is covered.

5. **All data loaded in a single scrape window (STRUCTURAL RISK).** 95%+ of rows have `inserted_at` in June 2026. No historical comp data before October 2025. No refresh cycle exists yet — data will age out as market moves.

---

## 1. Set-Level Coverage Table

| Set | Edition | Catalog Cards | Cards w/ Data | Coverage % | Total Comps | Date Range |
|---|---|---|---|---|---|---|
| Base Set | 1st Edition | 103 | 103 | **100.0%** | 3,318 | Nov 2025–Jun 2026 |
| Base Set | Shadowless | 102 | 101 | **99.0%** | 1,439 | Oct 2025–Jun 2026 |
| Base Set | Unlimited | 103 | 99 | 96.1% | 889 | Nov 2025–Jun 2026 |
| Best of Game | N/A | 11 | 0 | **0.0%** | 0 | — |
| Best of Game | Unlimited | 7 | 0 | **0.0%** | 0 | — |
| Fossil | 1st Edition | 62 | 62 | **100.0%** | 3,539 | Oct 2025–Jun 2026 |
| Fossil | N/A | 2 | 0 | **0.0%** | 0 | — |
| Fossil | Unlimited | 64 | 62 | 96.9% | 373 | Jan–Jun 2026 |
| Gym Challenge | 1st Edition | 132 | 131 | 99.2% | 1,404 | Oct 2025–Jun 2026 |
| Gym Challenge | Unlimited | 132 | 76 | **57.6%** | 188 | Mar–Jun 2026 |
| Gym Heroes | 1st Edition | 132 | 131 | 99.2% | 1,209 | Nov 2025–Jun 2026 |
| Gym Heroes | N/A | 1 | 0 | 0.0% | 0 | — |
| Gym Heroes | Unlimited | 132 | 76 | **57.6%** | 135 | Oct 2025–Jun 2026 |
| Jungle | 1st Edition | 64 | 64 | **100.0%** | 3,245 | Nov 2025–Jun 2026 |
| Jungle | N/A | 3 | 0 | 0.0% | 0 | — |
| Jungle | Unlimited | 80 | 63 | 78.8% | 279 | Mar–Jun 2026 |
| Team Rocket | 1st Edition | 84 | 84 | **100.0%** | 2,817 | Jun 2024–Jun 2026 |
| Team Rocket | N/A | 3 | 0 | 0.0% | 0 | — |
| Team Rocket | Unlimited | 84 | 68 | 81.0% | 281 | Mar–Jun 2026 |
| Wizards Black Star Promos | N/A | 53 | 3 | **5.7%** | 3 | Apr–May 2026 |

---

## 2. Top 50 Missing Cards (Zero Data — Urgent Fill List)

Cards ordered by expected market liquidity (high-value sets and low card numbers first).

### Best of Game — ALL 18 variants missing (promo re-scrape required)
All BOG cards have zero comps. Root cause: N/A edition bug. Re-scrape with fixed URL.

| card_code | Card | Edition | Variant |
|---|---|---|---|
| BOG001-UN-STN | Electabuzz | Unlimited | Standard |
| BOG002-UN-STN | Hitmonchan | Unlimited | Standard |
| BOG003-UN-STN | Professor Elm | Unlimited | Standard |
| BOG004-UN-STN | Rocket's Scizor | Unlimited | Standard |
| BOG005-UN-STN | Rocket's Sneasel | Unlimited | Standard |
| BOG001-NA-WIN | Electabuzz | N/A | Winner |
| BOG002-NA-WIN | Hitmonchan | N/A | Winner |
| BOG004-NA-WIN | Rocket's Scizor | N/A | Winner |
| BOG005-NA-WIN | Rocket's Sneasel | N/A | Winner |
| BOG006-NA-WIN/JUMBO | Dark Ivysaur | N/A | Winner/Jumbo |
| BOG007-NA-WIN/JUMBO | Dark Venusaur | N/A | Winner/Jumbo |
| BOG008-NA-WIN/JUMBO | Rocket's Mewtwo | N/A | Winner/Jumbo |
| BOG009-NA-WIN | Rocket's Hitmonchan | N/A | Winner |

### Team Rocket Unlimited — Liquid cards, missing data
| card_code | Card |
|---|---|
| TRK005-UN-STN | Dark Dragonite |
| TRK008-UN-STN | Dark Gyarados |
| TRK009-UN-STN | Dark Hypno |
| TRK016-UN-STN | Rocket's Sneak Attack |
| TRK034-UN-STN | Dark Electrode |
| TRK044-UN-STN | Dark Rapidash |

### Base Set Unlimited — Missing variants
| card_code | Card |
|---|---|
| BAS004-UN-C2000 | Charizard (1999-2000 Copyright) |
| BAS078-UN-STN | Scoop Up |
| BAS092-UN-STN | Energy Removal |
| BAS094-UN-STN | Potion |

### Gym Heroes / Gym Challenge Unlimited — 112 missing cards
These are predominantly Trainer cards (stadiums, energy, supporters) with thin eBay graded markets. Full list in Q2 raw output. Actionable priority cards:
- GYH006-UN-STN: Lt. Surge's Electabuzz
- GYH008-UN-STN: Lt. Surge's Magneton
- GYH013-UN-STN: Rocket's Scyther
- GYC007-UN-STN: Giovanni's Nidoking
- GYC012-UN-STN: Misty's Golduck
- GYC020-UN-STN: Sabrina

### Promo / Special Variants (genuinely thin markets)
| card_code | Card | Note |
|---|---|---|
| BAS092-SH-STN | Energy Removal | Shadowless — expected thin |
| FOS001-NA-PRE | Aerodactyl | Prerelease stamp |
| FOS050-NA-WSTAMP | Kabuto | W Stamp |
| FOS051-UN-ERR | Krabby | Error variant |
| JUN001-NA-PRE | Clefable | Prerelease stamp |
| JUN056-NA-GOLD | Meowth | Gold Border |
| JUN060-NA-WSTAMP | Pikachu | W Stamp |
| TRK008-NA-PRE | Dark Gyarados | Prerelease stamp |
| WBS001–053 | 50 promos | N/A edition bug — re-scrape required |

---

## 3. Grader Coverage — Bias Findings

| Set | Edition | PSA | CGC | BGS | SGC | TAG | Bias? |
|---|---|---|---|---|---|---|---|
| Base Set | 1st Edition | 2,494 | 631 | 95 | 8 | 6 | PSA dominant — normal for high-value |
| Base Set | Shadowless | 945 | 307 | 109 | 10 | 9 | Balanced across top 3 |
| Base Set | Unlimited | 624 | 123 | 42 | 26 | 24 | PSA dominant |
| Fossil | 1st Edition | 2,446 | 967 | 44 | 11 | 11 | PSA/CGC OK; BGS/SGC thin |
| Gym Challenge | 1st Edition | 988 | 326 | 50 | 4 | 10 | PSA dominant |
| Gym Challenge | Unlimited | 107 | 45 | 4 | 1 | 6 | **BGS/SGC nearly absent** |
| Gym Heroes | 1st Edition | 724 | 412 | 46 | 2 | 2 | PSA/CGC OK; BGS/SGC thin |
| Gym Heroes | Unlimited | 54 | 20 | 5 | 1 | 6 | **BGS/SGC nearly absent** |
| Jungle | 1st Edition | 2,441 | 697 | 34 | 12 | 8 | PSA dominant |
| Jungle | Unlimited | 211 | 30 | 3 | 2 | 1 | **CGC/BGS/SGC severely thin** |
| Team Rocket | 1st Edition | 1,646 | 956 | 101 | 13 | 26 | Best multi-grader coverage |
| Team Rocket | Unlimited | 137 | 43 | 18 | 11 | 11 | Reasonable for Unlimited |
| WBS Promos | N/A | 2 | 1 | 0 | 0 | 0 | **Complete blind spot** |

**Key findings:**
- PSA dominates everywhere — expected given market share, not a scraper bias.
- BGS and SGC are nearly absent from Unlimited across GYH, GYC, JGL. These graders have fewer Unlimited submissions overall; may be a genuine market gap rather than a scraper gap.
- TAG shows up sporadically — the TAG grader onboarding is working but volumes are correctly small.
- WBS Promos: only 3 comps across 2 PSA + 1 CGC. Complete re-scrape required.

---

## 4. Grade Distribution Check (PSA on Key Cards)

Sample of high-name cards — Base Set 1st Edition PSA:

| Card | Grades covered | PSA 10 | PSA 9 | PSA 8 | PSA 7 | Lower | Note |
|---|---|---|---|---|---|---|---|
| Charizard BAS004-1E | 10,9,8,7,6,5,4,3,2,1 | 1 | 3 | 1 | 5 | 12 | Low PSA 10 count — likely underscraping high grades |
| Blastoise BAS002-1E | 9,8,7,6,5,2 | 0 | 4 | 4 | 4 | 4 | **Missing PSA 10** entirely |
| Venusaur BAS015-1E | 9,8,7,6,5,3,2 | 0 | 3 | 2 | 4 | 6 | **Missing PSA 10** entirely |
| Mewtwo BAS010-1E | 10,9,8,7,6,5,4,1 | 3 | 2 | 5 | 3 | 8 | Thin at top |
| Pikachu JUN060-1E | 10,9,8,7,6,5,4,3,2,1 | 112 | 140 | 68 | 38 | 66 | Best coverage — Jungle Pikachu is popular |

**Finding:** PSA 10 Charizard 1E has only 1 comp. PSA 10 Blastoise/Venusaur have 0. These are the most liquid cards in the hobby — the scraper is systematically missing high-grade sales, likely because high-grade cards sell on premium eBay search pages the card-by-card scraper doesn't reach. The Chrome MCP bulk approach captured Pikachu well (112 PSA 10s) because it pulled all pages.

---

## 5. Timing Analysis

| Set | Edition | May 2026 | Jun 2026 | Note |
|---|---|---|---|---|
| Base Set | 1st Edition | 295 | 3,023 | ~9% loaded in May, rest from June Chrome MCP bulk |
| Base Set | Shadowless | 0 | 1,439 | Single June bulk scrape |
| Base Set | Unlimited | 0 | 889 | Single June bulk scrape |
| Fossil | 1st Edition | 30 | 3,509 | Single June bulk scrape |
| Fossil | Unlimited | 0 | 373 | Single June bulk scrape |
| Gym Challenge | 1st Edition | 75 | 1,329 | |
| Gym Challenge | Unlimited | 1 | 187 | |
| Gym Heroes | 1st Edition | 91 | 1,118 | |
| Gym Heroes | Unlimited | 0 | 135 | |
| Jungle | 1st Edition | 101 | 3,144 | |
| Jungle | Unlimited | 0 | 279 | |
| Team Rocket | 1st Edition | 497 | 2,320 | Best pre-June coverage (prior scraper runs) |
| Team Rocket | Unlimited | 0 | 281 | |
| WBS Promos | N/A | 0 | 3 | |

**Key finding:** All data is from a single scrape window (June 2026), with minor pre-work in May. There is no refresh cycle. Market prices drift weekly; comps from a single scrape window will go stale. A monthly refresh of the top-volume cards should be built into the n8n scheduling layer.

---

## 6. Shadowless Cross-Check

**✅ Not a blind spot.** Shadowless has 1,439 comps across 101/102 catalog cards (99%). The one missing card is `BAS092-SH-STN` (Energy Removal) — a genuinely thin-market trainer with essentially no graded sales on eBay.

---

## 7. Promos Cross-Check

| Set | Comps | Distinct Cards | Status |
|---|---|---|---|
| Best of Game | 0 | 0 | **BLIND SPOT — N/A edition bug** |
| Wizards Black Star Promos | 3 | 3 | **NEAR-BLIND SPOT — N/A edition bug** |

BOG was never scraped with a working URL. WBS got 3 rows (cards 2, 3, and one other) — these 3 came from pre-June scraping before the bug existed, or from Chrome MCP bulk passes that included promo titles incidentally.

---

## Recommended Scraper Re-runs

Priority order — do these before expanding to new sets:

| Priority | Action | Scope | Expected Yield | Notes |
|---|---|---|---|---|
| 1 — CRITICAL | Re-scrape BOG + WBS with N/A fix | `--sets BOG,WSP` (all editions) | ~50–200 comps | N/A fix deployed. In progress (promo scraper running, hitting 403s). |
| 2 — HIGH | Deepen Unlimited on GYH + GYC | `--sets GYH,GYC --edition Unlimited`, broaden search | ~200–400 comps | 64+/76 cards under 3 comps. Consider dropping "unlimited" term — Gym sets unmarked by sellers. |
| 3 — HIGH | Deepen Unlimited on JGL, TRK, FSL | `--sets JGL,TRK,FSL --edition Unlimited` | ~300–500 comps | 32–41 cards under 3 comps each. |
| 4 — MEDIUM | Fill high-grade 1E gaps (Charizard, Blastoise, Venusaur PSA 10) | Chrome MCP targeted: BAS 1E PSA/CGC high grades | ~20–50 comps | Card-by-card scraper doesn't reach premium listing pages. Use Chrome MCP bulk with grade filter. |
| 5 — MEDIUM | Refresh cycle | Top 200 cards by comp volume, monthly | Ongoing | No refresh cycle exists. Stale data risk grows weekly. |
| 6 — LOW | BST / JGL Unlimited No-Symbol variants | Targeted Chrome MCP for JUN-NOSYM variants | ~30–100 comps | 8 No-Symbol variants have zero data — separate print variant, may need different search term. |

---

## Recommendation: Expand to New Sets?

**No — not yet.**

**Rationale:**
- 231 catalog rows (17%) have zero data within the existing 8 sets.
- Unlimited coverage is shallow (57–81%) with most covered cards under 3 comps — not enough for reliable pricing.
- Promos (BOG, WBS) are essentially blank — 63+ cards with 0 comps.
- No refresh cycle exists; existing data is a single-point-in-time snapshot from June 2026.
- The card-by-card Node.js scraper is proving brittle against eBay 403s; Chrome MCP bulk scraping is higher-yield for wide coverage.

**When to expand:**
- All 8 existing sets at ≥85% card coverage with ≥3 comps per covered card.
- BOG + WBS promos fully scraped.
- A monthly refresh n8n workflow is live and running.
- The Unlimited search term issue is resolved (either proven to work or replaced with a broader approach).

Until those conditions are met, the marginal value of adding set 9 is lower than filling the existing gaps.
