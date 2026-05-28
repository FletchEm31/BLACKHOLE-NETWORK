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

`grader = NULL` is ambiguous between "we haven't captured this data yet" and "the card is ungraded / raw." HORIZON needs to distinguish those — a raw card is a market segment, missing data is a quality issue. Explicit sentinels:

| Scenario | `grader` | `grade` | `grade_label` |
|---|---|---|---|
| Graded card (any tier) | `PSA` / `CGC` / `BGS` / `SGC` | the raw_label (e.g. `10`, `Gem Mint 10`) | tier name from title |
| **Raw / ungraded card** (no slab) | **`RAW`** | NULL | `Ungraded` |
| Data not captured yet | NULL | NULL | NULL |

`RAW` is a first-class grader value across all fact tables. CHECK constraints on `ebay_transactions`, `courtyard_transactions`, `courtyard_asks`, `collector_crypt_transactions`, `collector_crypt_asks` accept it; master catalog row: `('RAW', 'Ungraded', NULL, 'Ungraded', FALSE, FALSE)`. Migration: `sql/migrations/2026-05-28-grade-catalog-corrections.sql`.

**Rules:**
- `grader = 'RAW'` is a positive assertion: the card IS ungraded. Distinct market segment from graded slabs — different price expectations, different sniping logic, different P&L treatment.
- `grader = NULL` is a deficit: we don't know. Parser failed or scraper didn't capture. Operator should investigate why.
- `grade = NULL` is acceptable when `grader = 'RAW'` (not applicable) OR when we couldn't determine the grade (missing).
- `grade_label = 'Ungraded'` only when `grader = 'RAW'`.

---

## CGC — 25 Raw Labels (authoritative 2026-05-28)
**Source:** operator-supplied authoritative table (2026-05-28). Supersedes prior label-color assumptions.

| raw_label | tier_label | numeric | label color | reholder_eligible | Notes |
|---|---|---|---|---|---|
| `Perfect 10` | `Perfect` | 10.0 | (legacy) | — | LEGACY — retired 2023. Kept for backfill only. |
| `Pristine 10` | `Pristine` | 10.0 | **Gold** | — | Current top tier. Distinct from Gem Mint. |
| `Gem Mint 10` | `Gem Mint` | 10.0 | **Blue** | — | Current standard 10. Distinct from Pristine. |
| `Gem Mint 9.5` | `Gem Mint` | 9.5 | **older Blue** | **TRUE** | **OUTLIER.** Older Blue Label naming. Market ≈ `Gem Mint 10`. Reholder → `Gem Mint 10` for ~$10. |
| `Mint+ 9.5` | `Mint+` | 9.5 | Blue | — | Current standard 9.5. **NOT equivalent to a 10.** |
| `9` | `Mint` | 9.0 | Blue | — | Raw label IS the bare number for grades 1–9 |
| `8.5` | `Near Mint/Mint+` | 8.5 | Blue | — | |
| `8` | `Near Mint/Mint` | 8.0 | Blue | — | |
| `7.5` | `Near Mint+` | 7.5 | Blue | — | |
| `7` | `Near Mint` | 7.0 | Blue | — | |
| `6.5` | `Excellent/Mint+` | 6.5 | Blue | — | |
| `6` | `Excellent/Mint` | 6.0 | Blue | — | |
| `5.5` | `Excellent+` | 5.5 | Blue | — | |
| `5` | `Excellent` | 5.0 | Blue | — | |
| `4.5` | `Very Good/Excellent+` | 4.5 | Blue | — | |
| `4` | `Very Good/Excellent` | 4.0 | Blue | — | |
| `3.5` | `Very Good+` | 3.5 | Blue | — | |
| `3` | `Very Good` | 3.0 | Blue | — | |
| `2.5` | `Good+` | 2.5 | Blue | — | |
| `2` | `Good` | 2.0 | Blue | — | |
| `1.5` | `Fair` | 1.5 | Blue | — | |
| `1` | `Poor` | 1.0 | Blue | — | |
| `AU` | `Altered/Ungraded` | NULL | (special) | — | Altered or unauthentic — no numeric grade |
| `AA` | `Altered/Authentic` | NULL | (special) | — | Authentic but altered — no numeric grade |

Parser-fallback rows also live in the live catalog but are not authoritative tiers:
- `10` (bare, no tier) — ambiguous CGC 10 catch-all; routes to `grade_reject_log` per §3.8.
- `9.5` (bare, no tier) — same idea for the 9.5 tier when no `Mint+` or `Gem Mint` label in title.

**CGC label colors (current scheme):**

| Color | What it means | Raw_labels carrying this color |
|---|---|---|
| **Blue** | Standard CGC grading | `Gem Mint 10`, `Mint+ 9.5`, plus the bare-number / shorthand tiers `9`–`1` |
| **Gold** | Pristine tier (stricter criteria at 10) | `Pristine 10` |
| (older Blue) | Outlier — legacy 9.5 from a prior naming convention | `Gem Mint 9.5` |
| (legacy) | Perfect tier, retired 2023 | `Perfect 10` |

There is **no current CGC Green Label.** Earlier drafts that referenced one were incorrect.

**CRITICAL CGC NOTE:** For grades 1–9, the raw_label stored in the DB IS the bare number
(`'9'`, `'8.5'`, `'8'` etc.) — NOT a text tier name. `Mint+ 9.5` and `Gem Mint 9.5` are the
only non-bare labels at 9.5. The 10-tier raw_labels (`Perfect 10`, `Pristine 10`, `Gem Mint 10`)
are all distinct rows.

**Ambiguity rules:**
- Bare `CGC 10` in a listing title with no tier name → cannot be resolved between `Pristine 10` and `Gem Mint 10`. Route to `grade_reject_log`.
- Bare `CGC 9.5` in a listing title with no tier name → cannot be resolved between `Mint+ 9.5` (current Blue) and `Gem Mint 9.5` (legacy older Blue). Route to `grade_reject_log`. **This matters more than the 10 case** because the two 9.5 tiers price differently (Mint+ 9.5 ≠ Gem Mint 10, while Gem Mint 9.5 ≈ Gem Mint 10).
- Title containing `Pristine` → `grade = 'Pristine 10'`. `Gem Mint` + `10` → `'Gem Mint 10'`. `Gem Mint` + `9.5` → `'Gem Mint 9.5'`. `Mint+` + `9.5` → `'Mint+ 9.5'`.

### CGC Reholder/Crossover Service — HORIZON arbitrage signal

CGC offers a paid reholder/crossover service. The relevant legacy row:

| raw_label | numeric | reholder target | Fee | Reason |
|---|---|---|---|---|
| `Gem Mint 9.5` | 9.5 | `Gem Mint 10` (current Blue Label) | ~$10 | Same grading evaluation, modern label format |

**Pricing implications HORIZON should flag:**
- Legacy `Gem Mint 9.5` slabs may trade at a **discount** vs native `Gem Mint 10`s — buyers know they can upgrade for ~$10.
- Post-reholder slabs are **indistinguishable** from native 10s; no provenance trail in the cert number.
- Arbitrage: **buy `Gem Mint 9.5` at discount → reholder ($10) → sell as `Gem Mint 10`**.

`master_grade_catalog.reholder_eligible BOOLEAN` flags eligible raw_labels. Added via
`sql/migrations/2026-05-28-grade-catalog-corrections.sql`. Currently flagged TRUE for `CGC | Gem Mint 9.5` only.

**Deferred (operator review):**
- Does CGC reholder `Perfect 10` (legacy 2023-retired) → current top tier? If yes, flag.
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

## BGS (Beckett) — 22 Raw Labels (post-Gold Label addition)
**Source:** master_grade_catalog (21 rows live as of 2026-05-28, +1 added via the grade-catalog-corrections migration). BGS scraper not yet built.
Claude Code must verify exact raw_labels against live DB before using.

| grade (raw_label) | grade_label | grade_numeric | label color | Notes |
|---|---|---|---|---|
| `Pristine 10` | `Pristine` | 10.0 | **Black** | BGS top tier — all four subgrades = 10. Extremely rare. |
| `Gold 10` | `Gold` | 10.0 | **Gold** | Overall 10, at least one subgrade below 10. Less rare than Black; still scarce. Premium over standard BGS, discount vs Black. |
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
- `Pristine 10` (Black Label) requires all four subgrades to be 10 — extremely rare
- `Gold 10` (Gold Label) is overall 10 with one or more subgrades below 10 — less rare than Black
- `Gem Mint 9.5` is the practical top for most slabs (standard white/silver label)
- In eBay titles: `BGS 9.5` always means `Gem Mint 9.5`. No ambiguity.
- Titles may say `BECKETT` instead of `BGS` — detect both, store grader as `BGS`

**BGS label colors:**

| Color | What it means | Raw_label |
|---|---|---|
| **Black** | Perfect-10 slab (all four subgrades = 10) | `Pristine 10` |
| **Gold** | 10-overall slab with at least one sub-10 subgrade | `Gold 10` |
| (standard) | Everything else (≤ 9.5 overall, or 10 not meeting Black/Gold criteria) | All other raw_labels — white/silver slab |

**Title parsing:** `BLACK LABEL` or `PRISTINE` + `BGS 10` → `Pristine 10`. `GOLD LABEL` + `BGS 10` → `Gold 10`. Bare `BGS 10` with no label keyword → ambiguous; route to grade_reject_log until clarified.

**Reholder/crossover:** No confirmed BGS equivalent of CGC's reholder service. Operator to confirm if Beckett offers a Black/Gold Label crossover; flag deferred.

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

