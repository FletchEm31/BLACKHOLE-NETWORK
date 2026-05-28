# BHN Grade Label Reference
## grade · grade_label · grade_numeric — Complete Vocabulary per Grader

**Authority:** `infrastructure/scrapers/cgc-pop-scrape.js` (CGC GRADE_MAP — live, deployed)
and `infrastructure/scrapers/psa-pop-scrape.js` (PSA GRADE_MAP — built, not yet in prod).
BGS and SGC scrapers not yet built — labels sourced from grader documentation.
Live DB (`master_grade_catalog`) is ground truth — Claude Code must verify all rows against:
`SELECT grader, raw_label, numeric_grade, tier_label FROM master_grade_catalog ORDER BY grader, numeric_grade DESC;`

---

## How the Three Columns Work Together

| Column | Stored? | Source | Example |
|--------|---------|--------|---------|
| `grade` | YES — on fact rows | Verbatim raw_label, FK-enforced | `Gem Mint 10` |
| `grade_label` | YES — on fact rows | Parsed from listing title | `Gem Mint` |
| `grade_numeric` | NO — never stored | Derived via JOIN to master_grade_catalog | `10.0` |

`grade_label` is the tier name only — no number, no grader. It is:
- Populated when the seller includes the tier name in the listing title
- NULL/empty when the title only has a bare number (e.g. "PSA 9")
- Used to resolve ambiguous grades (e.g. CGC 10 → Pristine vs Gem Mint)
- A human-readable display aid in dashboards and alerts

---

## Grader Sentinel Values — Disambiguating NULL

`grader = NULL` is ambiguous between "we haven't captured this data yet," "the card is ungraded," and "we couldn't parse the grade." HORIZON needs to distinguish all three — a raw card is a market segment, an unparseable grade is a parser issue, missing data is a scraper issue. Explicit sentinels:

| Scenario | `grader` | `grade` | `grade_label` | Lives in |
|---|---|---|---|---|
| Graded card (any tier) | `PSA` / `CGC` / `BGS` / `SGC` | the raw_label (e.g. `10`, `Gem Mint 10`) | tier name from title | Graded fact tables |
| **Raw / ungraded card** (no slab) | **`RAW`** | NULL | `Ungraded` | **`raw_*` tables** — see "RAW (Ungraded)" section below |
| **Grade unparseable from title** | **`UNKNOWN`** | NULL | NULL | Graded fact tables — flag for operator review |
| Data not captured yet | NULL | NULL | NULL | Anywhere — scraper/parser gap |

`RAW` and `UNKNOWN` are first-class grader values. CHECK constraints on `ebay_transactions`, `courtyard_transactions`, `courtyard_asks`, `collector_crypt_transactions`, `collector_crypt_asks` accept both (and PSA/CGC/BGS/SGC). Master catalog rows:
- `('RAW', 'Ungraded', NULL, 'Ungraded', FALSE, FALSE)`
- `('UNKNOWN', 'Unparseable', NULL, 'Unparseable', FALSE, FALSE)`

Migration: `sql/migrations/2026-05-28-grade-catalog-corrections.sql`.

**Rules:**
- `grader = 'RAW'` is a positive assertion: the card IS ungraded. **Lives in `raw_*` tables**, NOT graded fact tables.
- `grader = 'UNKNOWN'` means we observed a listing but the grade was unparseable. Row still lands in graded fact tables for replay/review.
- `grader = NULL` is a deficit: we don't know. Parser/scraper gap. Operator should investigate.
- `grade = NULL` is acceptable when `grader IN ('RAW','UNKNOWN')` (not applicable / can't determine) OR when data is genuinely missing.
- `grade_label = 'Ungraded'` only when `grader = 'RAW'`.

---

## CGC — 25 Raw Labels (authoritative 2026-05-28)
**Source:** operator-supplied authoritative table (2026-05-28). Supersedes prior label-color assumptions.

| raw_label | tier_label | numeric | label color | reholder_eligible | Notes |
|---|---|---|---|---|---|
| `Perfect 10` | `Perfect` | 10.0 | (legacy) | **TRUE** | LEGACY — retired 2023. Reholders to `Pristine 10` (**NOT** `Gem Mint 10`). Fee unknown. Kept for backfill only. |
| `Pristine 10` | `Pristine` | 10.0 | **Gold** | — | Current top tier. Gold Label. Virtually flawless. 50/50 centering required. Distinct from Gem Mint. |
| `Gem Mint 10` | `Gem Mint` | 10.0 | **Black** | — | Current standard 10. One criteria short of Pristine. |
| `Gem Mint 9.5` | `Gem Mint` | 9.5 | **Blue** (legacy) | **TRUE** | **OUTLIER.** Legacy Blue Label naming. CGC officially treats as equivalent to `Gem Mint 10`. Reholder → `Gem Mint 10` for **$5–$10**. |
| `Mint+ 9.5` | `Mint+` | 9.5 | **Black** | — | Current standard 9.5. **NOT equivalent to a 10.** Completely different card from Gem Mint 9.5. |
| `9` | `Mint` | 9.0 | Black | — | Raw label IS the bare number for grades 1–9 |
| `8.5` | `Near Mint/Mint+` | 8.5 | Black | — | |
| `8` | `Near Mint/Mint` | 8.0 | Black | — | |
| `7.5` | `Near Mint+` | 7.5 | Black | — | |
| `7` | `Near Mint` | 7.0 | Black | — | |
| `6.5` | `Excellent/Mint+` | 6.5 | Black | — | |
| `6` | `Excellent/Mint` | 6.0 | Black | — | |
| `5.5` | `Excellent+` | 5.5 | Black | — | |
| `5` | `Excellent` | 5.0 | Black | — | |
| `4.5` | `Very Good/Excellent+` | 4.5 | Black | — | |
| `4` | `Very Good/Excellent` | 4.0 | Black | — | |
| `3.5` | `Very Good+` | 3.5 | Black | — | |
| `3` | `Very Good` | 3.0 | Black | — | |
| `2.5` | `Good+` | 2.5 | Black | — | |
| `2` | `Good` | 2.0 | Black | — | |
| `1.5` | `Fair` | 1.5 | Black | — | |
| `1` | `Poor` | 1.0 | Black | — | |
| `AU` | `Altered/Ungraded` | NULL | (special) | — | Altered or unauthentic — no numeric grade |
| `AA` | `Altered/Authentic` | NULL | (special) | — | Authentic but altered — no numeric grade |

Parser-fallback rows also live in the live catalog but are not authoritative tiers:
- `10` (bare, no tier) — ambiguous CGC 10 catch-all; routes to `grade_reject_log` per §3.8.
- `9.5` (bare, no tier) — same idea for the 9.5 tier when no `Mint+` or `Gem Mint` label in title.

**CGC label colors (authoritative 2026-05-28):**

| Color | What it means | Raw_labels carrying this color |
|---|---|---|
| **Gold** | Pristine tier — virtually flawless, 50/50 centering | `Pristine 10` |
| **Black** | Current standard CGC grading | `Gem Mint 10`, `Mint+ 9.5`, plus bare-number tiers `9`–`1` |
| **Blue** | LEGACY — older naming convention outlier (pre-2023 era) | `Gem Mint 9.5` only |
| (legacy) | Retired 2023 — kept for backfill | `Perfect 10` |

There is **no current CGC Green Label for trading cards.** Earlier session drafts that referenced one were incorrect and have been corrected.

**CRITICAL CGC NOTE:** For grades 1–9, the raw_label stored in the DB IS the bare number
(`'9'`, `'8.5'`, `'8'` etc.) — NOT a text tier name. `Mint+ 9.5` and `Gem Mint 9.5` are the
only non-bare labels at 9.5. The 10-tier raw_labels (`Perfect 10`, `Pristine 10`, `Gem Mint 10`)
are all distinct rows.

**Ambiguity rules:**
- Bare `CGC 10` in a listing title with no tier name → cannot be resolved between `Pristine 10` and `Gem Mint 10`. Route to `grade_reject_log`.
- Bare `CGC 9.5` in a listing title with no tier name → cannot be resolved between `Mint+ 9.5` (current Blue) and `Gem Mint 9.5` (legacy older Blue). Route to `grade_reject_log`. **This matters more than the 10 case** because the two 9.5 tiers price differently (Mint+ 9.5 ≠ Gem Mint 10, while Gem Mint 9.5 ≈ Gem Mint 10).
- Title containing `Pristine` → `grade = 'Pristine 10'`. `Gem Mint` + `10` → `'Gem Mint 10'`. `Gem Mint` + `9.5` → `'Gem Mint 9.5'`. `Mint+` + `9.5` → `'Mint+ 9.5'`.

### CGC Reholder/Crossover Service — HORIZON arbitrage signal

CGC offers a paid reholder/crossover service. **Two flagged cases:**

| Source raw_label | Source label color | Reholder target | Target label color | Fee | Notes |
|---|---|---|---|---|---|
| `Gem Mint 9.5` | Blue (legacy) | `Gem Mint 10` | Black (current) | **$5–$10** | CGC officially treats as equivalent grade. Same eval, modern label format. |
| `Perfect 10` | (legacy retired 2023) | `Pristine 10` | Gold (current) | unknown | **Reholders to Pristine 10, NOT Gem Mint 10.** Operator to confirm fee. |

**Pricing implications HORIZON should flag:**
- Legacy `Gem Mint 9.5` slabs may trade at a **discount** vs native `Gem Mint 10`s — buyers know they can upgrade for $5–$10.
- Post-reholder slabs are **indistinguishable** from native 10s; no provenance trail in the cert number.
- Arbitrage: **buy `Gem Mint 9.5` at discount → reholder ($5–$10) → sell as `Gem Mint 10`**.

**Schema:** `master_grade_catalog` carries four reholder columns:

| Column | Type | Purpose |
|---|---|---|
| `reholder_eligible` | BOOLEAN | TRUE on raw_labels eligible for crossover |
| `reholder_target_raw_label` | TEXT | What it becomes post-reholder |
| `reholder_fee_min_usd` | NUMERIC | Lower bound of fee (NULL when unknown) |
| `reholder_fee_max_usd` | NUMERIC | Upper bound of fee |

Migration: `sql/migrations/2026-05-28-grade-catalog-corrections.sql`.

**Deferred (operator review):**
- `Perfect 10` reholder fee — operator to confirm exact rate
- SGC equivalent crossover service — does one exist? Flag if so.

---

## PSA — 20 Raw Labels
**Source:** `psa-pop-scrape.js` GRADE_MAP (authoritative — maps directly from PSA's API fields)

| grade (raw_label) | grade_label | grade_numeric | Notes |
|-------------------|-------------|---------------|-------|
| `10` | `Gem Mint` | 10.0 | PSA raw label for 10 IS the bare `'10'` |
| `9` | `Mint` | 9.0 | PSA raw label for 9 IS the bare `'9'` |
| `8.5` | `NM-MT+` | 8.5 | |
| `8` | `NM-MT` | 8.0 | Near Mint–Mint |
| `7.5` | `NM+` | 7.5 | |
| `7` | `NM` | 7.0 | Near Mint |
| `6.5` | `EX-MT+` | 6.5 | |
| `6` | `EX-MT` | 6.0 | Excellent–Mint |
| `5.5` | `EX+` | 5.5 | |
| `5` | `EX` | 5.0 | Excellent |
| `4.5` | `VG-EX+` | 4.5 | |
| `4` | `VG-EX` | 4.0 | Very Good–Excellent |
| `3.5` | `VG+` | 3.5 | |
| `3` | `VG` | 3.0 | Very Good |
| `2.5` | `GOOD+` | 2.5 | |
| `2` | `GOOD` | 2.0 | |
| `1.5` | `FAIR` | 1.5 | |
| `1` | `POOR` | 1.0 | |
| `1.5` | `FR` | 1.5 | Fair — same numeric as FAIR |
| `Authentic` | `Authentic` | NULL | Genuine card, no condition grade |

**CRITICAL PSA NOTE:** PSA raw labels for grades 1–10 are ALL bare numbers (`'10'`, `'9'`,
`'8.5'` etc.) — NOT text tier names. This is confirmed by the PSA pop scraper GRADE_MAP.
Unlike CGC, PSA has NO ambiguity at grade 10 — there is only one PSA 10 tier.
PSA has no 9.5 tier. PSA qualifiers (OC, MC, ST, MK, PD, OF) are tracked in
master_grading_criteria_catalog but do NOT appear as separate grade rows.

**Title parsing for grade_label:** When an eBay title contains `GEM MINT` alongside `PSA 10`,
set grade_label = `Gem Mint`. When it contains `MINT` alongside `PSA 9`, set grade_label = `Mint`.
The label is informational only — PSA grade resolution is unambiguous from the number alone.

---

## BGS (Beckett) — 23 Raw Labels (post-Black/Gold Label additions)
**Source:** master_grade_catalog (21 rows live as of 2026-05-28, +2 added via the grade-catalog-corrections migration: `Black Label 10` and `Gold Label 10`). BGS scraper not yet built.
Claude Code must verify exact raw_labels against live DB before using.

| grade (raw_label) | grade_label | grade_numeric | label color | Notes |
|---|---|---|---|---|
| `Black Label 10` | `Black Label` | 10.0 | **Black** | BGS top tier — all four subgrades = 10. Rarest, highest premium. |
| `Gold Label 10` | `Gold Label` | 10.0 | **Gold** | Overall 10, subgrades may include 9.5. Less rare than Black; premium over Pristine. |
| `Pristine 10` | `Pristine` | 10.0 | (standard top) | Overall 10, less-strict subgrade criteria. Lowest of the three 10-tiers, still scarce. |
| `Gem Mint 9.5` | `Gem Mint` | 9.5 | (standard) | Subgrades all 9.5+ |
| `Mint 9` | `Mint` | 9.0 | |
| `Near Mint-Mint+ 8.5` | `Near Mint-Mint+` | 8.5 | |
| `Near Mint-Mint 8` | `Near Mint-Mint` | 8.0 | |
| `Near Mint+ 7.5` | `Near Mint+` | 7.5 | |
| `Near Mint 7` | `Near Mint` | 7.0 | |
| `Excellent-Mint+ 6.5` | `Excellent-Mint+` | 6.5 | |
| `Excellent-Mint 6` | `Excellent-Mint` | 6.0 | |
| `Excellent+ 5.5` | `Excellent+` | 5.5 | |
| `Excellent 5` | `Excellent` | 5.0 | |
| `Very Good-Excellent+ 4.5` | `Very Good-Excellent+` | 4.5 | |
| `Very Good-Excellent 4` | `Very Good-Excellent` | 4.0 | |
| `Very Good+ 3.5` | `Very Good+` | 3.5 | |
| `Very Good 3` | `Very Good` | 3.0 | |
| `Good+ 2.5` | `Good+` | 2.5 | |
| `Good 2` | `Good` | 2.0 | |
| `Fair 1.5` | `Fair` | 1.5 | |
| `Poor 1` | `Poor` | 1.0 | |
| `Authentic` | `Authentic` | NULL | |
| `BGS 0.5` | `Incomplete` | 0.5 | Incomplete card — missing pieces |

**BGS-specific notes:**
- BGS publishes subgrades (Centering / Corners / Edges / Surface) — the only grader that does
- **Three-tier top-end** at overall 10: `Black Label 10` > `Gold Label 10` > `Pristine 10`
  - `Black Label 10` requires all four subgrades = 10 — rarest, highest premium
  - `Gold Label 10` is overall 10 with subgrades that may include 9.5 — middle tier
  - `Pristine 10` is overall 10 with less-strict subgrade criteria — lowest of the three 10-tiers
- `Gem Mint 9.5` is the practical top for most slabs (standard white/silver label)
- In eBay titles: `BGS 9.5` always means `Gem Mint 9.5`. No ambiguity at 9.5.
- Bare `BGS 10` with no label keyword is ambiguous across the three 10-tiers → route to grade_reject_log until label is in title
- Titles may say `BECKETT` instead of `BGS` — detect both, store grader as `BGS`

**BGS label colors:**

| Color | What it means | Raw_label |
|---|---|---|
| **Black** | Perfect-10 slab (all four subgrades = 10) | `Black Label 10` |
| **Gold** | 10-overall slab with at least one subgrade at 9.5 | `Gold Label 10` |
| (standard top) | 10-overall slab not meeting Black/Gold criteria | `Pristine 10` |
| (standard) | Everything ≤ 9.5 | All other raw_labels — white/silver slab |

**Title parsing:** `BLACK LABEL` + `BGS 10` → `Black Label 10`. `GOLD LABEL` + `BGS 10` → `Gold Label 10`. `PRISTINE` + `BGS 10` → `Pristine 10`. Bare `BGS 10` with no label keyword → ambiguous; route to grade_reject_log.

> ⚠ **Naming convention pending operator confirmation:** raw_labels above use `Black Label 10` / `Gold Label 10`. If BGS prints `Black 10` / `Gold 10` on the slab instead, update master_grade_catalog and parsers accordingly. Easy rename if needed.

**Reholder/crossover:** No confirmed BGS equivalent of CGC's reholder service. Operator to confirm if Beckett offers a Black/Gold/Pristine crossover; flag deferred.

---

## SGC — 22 Raw Labels
**Source:** master_grade_catalog (22 rows confirmed). SGC scraper not yet built.
Claude Code must verify exact raw_labels against live DB before using.

| grade (raw_label) | grade_label | grade_numeric | Notes |
|-------------------|-------------|---------------|-------|
| `Pristine 10` | `Pristine` | 10.0 | SGC top tier |
| `Gem Mint 10` | `Gem Mint` | 10.0 | Distinct from Pristine |
| `Gem Mint 9.5` | `Gem Mint` | 9.5 | |
| `Gem Mint 9` | `Gem Mint` | 9.0 | SGC uses Gem Mint down to 9 |
| `Near Mint/Mint+ 8.5` | `Near Mint/Mint+` | 8.5 | |
| `Near Mint/Mint 8` | `Near Mint/Mint` | 8.0 | |
| `Near Mint+ 7.5` | `Near Mint+` | 7.5 | |
| `Near Mint 7` | `Near Mint` | 7.0 | |
| `Excellent/Near Mint+ 6.5` | `Excellent/Near Mint+` | 6.5 | |
| `Excellent/Near Mint 6` | `Excellent/Near Mint` | 6.0 | |
| `Excellent+ 5.5` | `Excellent+` | 5.5 | |
| `Excellent 5` | `Excellent` | 5.0 | |
| `Very Good/Excellent+ 4.5` | `Very Good/Excellent+` | 4.5 | |
| `Very Good/Excellent 4` | `Very Good/Excellent` | 4.0 | |
| `Very Good+ 3.5` | `Very Good+` | 3.5 | |
| `Very Good 3` | `Very Good` | 3.0 | |
| `Good+ 2.5` | `Good+` | 2.5 | |
| `Good 2` | `Good` | 2.0 | |
| `Fair 1.5` | `Fair` | 1.5 | |
| `Poor 1.5` | `Poor` | 1.5 | |
| `Poor 1` | `Poor` | 1.0 | |
| `Authentic` | `Authentic` | NULL | |

**SGC-specific notes:**
- SGC uses a legacy 1–100 numeric scale on older slabs — the PSA scraper already handles this:
  100→10, 98→10, 96→9.5, 92→9, 88→8.5, 84→8, 80→7.5. Normalize at parse time.
- SGC 10 has TWO tiers: `Pristine 10` and `Gem Mint 10` — ambiguous from bare `SGC 10`
  in a title with no label. Apply same rule as CGC 10: if no label in title → reject log.
- In eBay titles: `SGC 9` always means `Gem Mint 9` (SGC's naming convention).

---

## RAW (Ungraded)

Raw (ungraded) cards are tracked separately from graded cards. They have fundamentally different market dynamics — condition-driven instead of grade-driven, no slab provenance, different fee structures, different buyer profiles. Mixing them with graded market data would dilute arbitrage signal quality.

**Master catalog row:**

| grader | raw_label | numeric_grade | tier_label | Notes |
|---|---|---|---|---|
| `RAW` | `Ungraded` | NULL | `Ungraded` | Unslabbed card. Condition tracked separately via `condition_label`. Never mixed with graded market tables. |

**Table series** (`raw_transactions`, `raw_asks`, `raw_bids`) — to be created when operator confirms architecture (single-set with `market` column vs per-market sets). See `collectibles-data-standard.md` §3.5.4.

**`condition_label` vocabulary** (replaces grader/grade/grade_label for raw cards):

| Code | Meaning |
|---|---|
| `NM` | Near Mint — top condition for ungraded |
| `LP` | Light Play — minor wear |
| `MP` | Moderate Play — visible wear |
| `HP` | Heavy Play — significant wear |
| `DMG` | Damaged — creased / torn / ink / liquid |

**Title parsing for raw cards:** Bare card listings without slab markers (PSA/CGC/BGS/SGC) and without numeric grade. Condition words in title (`NM`, `LP`, `Near Mint`, `Mint`, `Played`, etc.) map to `condition_label`.

---

## Scraper grade_label Extraction Rules

For each grader, these are the title keyword patterns the scraper should detect:

### CGC title patterns → grade_label
```
PRISTINE          → "Pristine"
GEM MINT          → "Gem Mint"
MINT+             → "Mint+"
(bare number only) → ""   ← grades 1-9 have no text label in CGC titles
```

### PSA title patterns → grade_label
```
GEM MINT / GEM-MINT  → "Gem Mint"
MINT                 → "Mint"       (careful: also in "Near Mint")
NM-MT / NM MT        → "NM-MT"
NM+                  → "NM+"
NEAR MINT            → "NM"
NM                   → "NM"
EX-MT / EX MT        → "EX-MT"
EX                   → "EX"
VG-EX / VG EX        → "VG-EX"
VG                   → "VG"
GOOD                 → "Good"
FAIR                 → "Fair"
POOR                 → "Poor"
AUTHENTIC / AUTH     → "Authentic"
```

### BGS title patterns → grade_label
```
PRISTINE             → "Pristine"
GEM MINT             → "Gem Mint"
NEAR MINT-MINT+      → "Near Mint-Mint+"
NEAR MINT-MINT / NM-MT → "Near Mint-Mint"
NEAR MINT+           → "Near Mint+"
NEAR MINT            → "Near Mint"
MINT                 → "Mint"
EXCELLENT-MINT+      → "Excellent-Mint+"
EXCELLENT-MINT       → "Excellent-Mint"
EXCELLENT            → "Excellent"
VERY GOOD            → "Very Good"
GOOD                 → "Good"
FAIR                 → "Fair"
POOR                 → "Poor"
```

### SGC title patterns → grade_label
```
PRISTINE             → "Pristine"
GEM MINT             → "Gem Mint"     (covers 9, 9.5, 10)
NEAR MINT/MINT+      → "Near Mint/Mint+"
NEAR MINT/MINT       → "Near Mint/Mint"
NEAR MINT+           → "Near Mint+"
NEAR MINT            → "Near Mint"
EXCELLENT            → "Excellent"
VERY GOOD            → "Very Good"
GOOD                 → "Good"
FAIR                 → "Fair"
POOR                 → "Poor"
AUTHENTIC            → "Authentic"
```

---

## Ambiguity Resolution Table

When grade_label is empty and only a bare number is in the title:

| Grader | Bare number | Ambiguous? | Rule |
|--------|-------------|------------|------|
| PSA | Any | NO | PSA raw labels ARE bare numbers. `10` → grade=`10`, no ambiguity. |
| CGC | 1–9 | NO | CGC raw labels ARE bare numbers for 1–9. `9` → grade=`9`. |
| CGC | 10 | YES | Could be Pristine 10 or Gem Mint 10. Must have label in title. → reject log if absent. |
| BGS | Any | NO | BGS always includes tier name in slab label. `9.5` → `Gem Mint 9.5`. |
| SGC | 1–8.5 | NO | Only one tier per numeric grade. |
| SGC | 9, 9.5, 10 | PARTIAL | SGC 9/9.5 = Gem Mint. SGC 10 could be Pristine or Gem Mint → reject log if no label. |

---

## For Claude Code — Verification Query

Run this on LA to confirm the exact raw_labels in the live DB before building any parser:

```sql
SELECT 
    grader,
    raw_label,
    numeric_grade,
    tier_label,
    market_equiv_10,
    is_authentic
FROM master_grade_catalog
ORDER BY grader, numeric_grade DESC NULLS LAST;
```

Expected output: 88 rows (CGC 25 / PSA 20 / BGS 21 / SGC 22).
If any row in this reference doc doesn't match the live DB output — the DB wins.
Update this doc to match, then update the scraper parser.

