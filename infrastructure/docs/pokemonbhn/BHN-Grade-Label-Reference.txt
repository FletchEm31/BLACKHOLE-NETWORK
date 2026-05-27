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

## CGC — 25 Raw Labels
**Source:** `cgc-pop-scrape.js` GRADE_MAP (authoritative — maps directly from CGC's live API fields)

| grade (raw_label) | grade_label | grade_numeric | Notes |
|-------------------|-------------|---------------|-------|
| `Perfect 10` | `Perfect` | 10.0 | LEGACY — retired 2023. Kept for backfill only. |
| `Pristine 10` | `Pristine` | 10.0 | Current top tier. Distinct from Gem Mint. |
| `Gem Mint 10` | `Gem Mint` | 10.0 | Current standard 10. Distinct from Pristine. |
| `Mint+ 9.5` | `Mint+` | 9.5 | |
| `9` | `Mint` | 9.0 | CGC raw label IS the bare number for grades 1–9 |
| `8.5` | `Near Mint/Mint+` | 8.5 | |
| `8` | `Near Mint/Mint` | 8.0 | |
| `7.5` | `Near Mint+` | 7.5 | |
| `7` | `Near Mint` | 7.0 | |
| `6.5` | `Fine/Near Mint+` | 6.5 | |
| `6` | `Fine/Near Mint` | 6.0 | |
| `5.5` | `Fine+` | 5.5 | |
| `5` | `Fine` | 5.0 | |
| `4.5` | `Very Good/Fine+` | 4.5 | |
| `4` | `Very Good/Fine` | 4.0 | |
| `3.5` | `Very Good+` | 3.5 | |
| `3` | `Very Good` | 3.0 | |
| `2.5` | `Good+` | 2.5 | |
| `2` | `Good` | 2.0 | |
| `1.5` | `Fair` | 1.5 | |
| `1` | `Poor` | 1.0 | |
| `AU` | `Altered/Ungraded` | NULL | Altered or unauthentic — no numeric grade |
| `AA` | `Altered/Authentic` | NULL | Authentic but altered — no numeric grade |

**CRITICAL CGC NOTE:** For grades 1–9, the raw_label stored in the DB IS the bare number
(`'9'`, `'8.5'`, `'8'` etc.) — NOT a text tier name. This matches exactly what the CGC API
returns and what the pop scraper stores. `Mint+ 9.5` is the only non-bare label below 10.
The three 10-tier labels (Perfect 10, Pristine 10, Gem Mint 10) are all distinct rows.

**Ambiguity rule:** A bare `CGC 10` in a listing title with no tier name cannot be resolved —
route to grade_reject_log. A title containing `Pristine` → `grade = 'Pristine 10'`,
containing `Gem Mint` → `grade = 'Gem Mint 10'`.

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

## BGS (Beckett) — 21 Raw Labels
**Source:** master_grade_catalog (21 rows confirmed). BGS scraper not yet built.
Claude Code must verify exact raw_labels against live DB before using.

| grade (raw_label) | grade_label | grade_numeric | Notes |
|-------------------|-------------|---------------|-------|
| `Pristine 10` | `Pristine` | 10.0 | BGS top tier — subgrades all 10 |
| `Gem Mint 9.5` | `Gem Mint` | 9.5 | Subgrades all 9.5+ |
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
- `Pristine 10` requires all four subgrades to be 10 — extremely rare
- `Gem Mint 9.5` is the practical top for most slabs
- Legacy Black Label (all subgrades 10, overall 10) maps to `Pristine 10` raw_label
- In eBay titles: `BGS 9.5` always means `Gem Mint 9.5`. No ambiguity.
- Titles may say `BECKETT` instead of `BGS` — detect both, store grader as `BGS`

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

