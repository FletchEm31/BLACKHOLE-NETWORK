# PokemonBHN — Collectibles Data Standard

**Status: AUTHORITATIVE.** This is the single source of truth for the PokemonBHN data domain —
table/column naming, canonical value formats, identity model, and enforcement rules. It is
written and maintained by Claude Code from the **live `eventhorizon` DB**. Where any other doc
(the `PokemonBHN_*` planning set, the retired `BHN-*` docs) disagrees with this file, **this file
wins**; where this file disagrees with the live DB, **the live DB wins** and this file is corrected.

Last verified against the live DB: **2026-05-21** (see [§9 Conformance status](#9-conformance-status)).

PokemonBHN is one of three data domains (PokemonBHN, FinancialBHN, SecurityBHN) inside the
**BLACKHOLE-NETWORK** repo, over shared infra (HORIZON, WireGuard, PostgreSQL, n8n).

---

## 1. Data model

Three **fact** streams (observations) conform to a set of shared **dimension** (authority) tables.
One **control** table drives a collector. Nothing fuses identity with observation.

### The Big 3 — facts (observations)
| Stream | Table | Captures | Truth-List role |
|--------|-------|----------|-----------------|
| Historical / backfill | `sold_listings` | **sold** price | value |
| Active listings | `ebay_listings` | **listed/asking** price | availability / deals |
| Population | `pop_reports` | grader **population** counts | scarcity |

### Dimensions — authorities (facts conform to these)
| Table | Authority for |
|-------|---------------|
| `master_card_catalog` (+ `card_catalog` compat view) | which cards exist (card identity) |
| `master_grade_catalog` | valid grades per grader |
| `master_grading_criteria_catalog` | grading criteria + qualifiers |
| `master_set_catalog` *(to build)* | sets: year, era, legal editions, count, PSA heading mapping |

### Control
| Table | Role |
|-------|------|
| `ebay_watchlist` | sniper buy-criteria (config, not observation) |

### Three concepts that must never fuse
| Concept | Identifies | Lives where | Cardinality |
|---------|-----------|-------------|-------------|
| `card_id` | a card-variant ("this kind of card exists") | `master_card_catalog.id` (serial PK) | 1,354 |
| `cert_number` | one physical graded slab | observation rows (not stored today) | thousands+ |
| `card_number` | within-set number (a **field**, not a key) | all tables | repeats per set |

Observations should resolve to `card_id` (the dumb surrogate). Do **not** encode meaning into the
key (no smart keys); a readable code like `BAS-004-1E-HOLO` is **derived for display only**.

---

## 2. Authorities & keys

- **`card_id`** = `master_card_catalog.id` — existing serial PK. Use it; never mint a parallel key.
  (Internal objects `card_catalog_id_seq` / `card_catalog_pkey` retain pre-rename names; harmless.)
- Card identity is the composite **`(set_name, card_number, edition, print_variant)`**, surfaced as `card_id`.
- `card_number` alone is **not** unique — a `4` exists in every set.

---

## 3. Canonical value formats (verified 2026-05-21)

### 3.1 `set_name` — 8 exact strings
`Base Set` · `Best of Game` · `Fossil` · `Gym Challenge` · `Gym Heroes` · `Jungle` ·
`Team Rocket` · `Wizards Black Star Promos`

### 3.2 `card_number` — bare integer
- Stored as **`text`**, value is a **bare integer** (`4`), never `#4`, never `4/102`.
- The denominator (set size) is **derived** from `master_set_catalog`, never stored.
- **Normalize-on-ingest** (every pipeline applies the same transform): strip leading `#`; take the
  numerator, drop `/denominator`; trim; drop leading zeros on pure-numeric; uppercase any letters.
  So `#4`, `4/102`, `PSA 4`, `004` all → `4`.
- Resolution key is **`(set_name, card_number)`** → `master_card_catalog`; an unresolved pair is
  flagged/quarantined, never silently kept.

### 3.3 Variant — SPLIT into `edition` + `print_variant`
The legacy single `variant` column (a grab-bag) is split into two orthogonal, controlled-vocabulary
columns. Both are **NOT NULL** (NULLs are distinct in a UNIQUE index and would break dedup).

- **`edition`** ∈ `{1st Edition, Unlimited, Shadowless, N/A}` — `N/A` reserved for promo sets
  (`master_set_catalog.is_promo`). Canonical token is `1st Edition` (never `1st Ed`).
- **`print_variant`** ∈ `{Standard, Holo, Error, No Symbol, W Stamp, Winner, Jumbo, Prerelease,
  Gold Border, Red Cheeks, WB Movie, Nintendo Power, WOTC, 1999-2000 Copyright}` — default `Standard`.
- Enforce both via CHECK or a small vocab table — **not free text**.
- Inherent holo (e.g. Base Charizard #4) is the card itself, **not** a `print_variant`; only
  *distinguishing* alternates get a non-`Standard` value.

### 3.4 `grader` — codes only
`{CGC, PSA, BGS, SGC}`. Never full descriptors (`Professional Sports Authenticator (PSA)`).

### 3.5 `grade` — verbatim raw_label
- `grade` = the exact label observed, and **must exist in `master_grade_catalog.raw_label`**
  (88 labels: CGC 25 / PSA 20 / BGS 21 / SGC 22). `numeric_grade`, `tier_label`, `market_equiv_10`
  are derived by JOIN — never stored on the fact.
- Raw / ungraded sales: **`grade = NULL`** (no placeholder).
- New labels must be added to `master_grade_catalog` **first** (deliberate vocab control).
- CGC `Perfect 10` is a **legacy** row (retired 2023) kept for backfill — not a current tier.

### 3.6 Money
- `listed_price` (asking) and `sold_price` (paid) are **separate**; valuation uses `sold_price` only.
- Money/shipping is **`NULL`, not `0`** (`0` means free/zero-sale).

### 3.7 Column-name conventions
- `card_number`, `set_name`, `card_name`, `grader`, `grade`, `edition`, `print_variant` — these exact
  names everywhere. **Known drift to fix:** `pop_reports` uses `card_set` (should be `set_name`).

---

## 4. Enforcement tiers (by table role)

| Table | Grade enforcement |
|-------|-------------------|
| `pop_reports`, `sold_listings` | **hard FK** `(grader, grade) → master_grade_catalog(grader, raw_label)` |
| `ebay_listings` | **soft validate-and-log** (live high-churn feed) |

The hard FK is all-or-nothing in a batch loader, so it **requires** a reject path:
- a **`grade_reject_log`** (one schema, shared by the FK batch-failure path and the soft path), and
- a **staging-filter** in `cgc-pop-load.js` **and** `cgc-pop-insert.js`: load to staging, divert rows
  whose grade isn't in the catalog to the reject log, insert only valid rows. An unknown label must
  **never** roll back a whole batch or silently disappear.

Composite FK is MATCH SIMPLE: rows with NULL `grader` or `grade` are skipped — so raw/ungraded
sales (`grade = NULL`) insert fine.

---

## 5. Grade collection scope
- **Active (`ebay_listings`):** grades **7 → top** of each scale (avoids empty queries).
- **Population (`pop_reports`):** **full scale** (a complete population picture is the point).
- `master_grade_catalog` always holds the full 1–10 scale — it's the dimension, independent of scope.

---

## 6. Truth List
A VIEW per card joining value (`sold_listings`) + availability (`ebay_listings`) + scarcity
(`pop_reports`). **Build last**, only after the streams are populated — confident-looking blanks are
worse than nothing.

---

## 7. Identity / sources (informational)
- `master_card_catalog` is external knowledge (the card roster); eBay/graders don't provide it.
- Historical sold prices: PriceCharting (7-yr) + 130point. eBay Browse API does **not** return sold prices.
- Active listings: eBay Browse API `item_summary/search` (search-only).
- Population: CGC scraper (deployed) + PSA (multi-heading, in progress); SGC/BGS where available.

---

## 8. Change control
- New `set_name`, `edition`, `print_variant`, `grade` label, or grader → add to the relevant
  `master_*` authority **before** any fact row can use it. The FK / CHECK enforces this.
- This doc is updated whenever the live DB changes; the DB is the tiebreaker.

---

## 9. Conformance status (target vs. live, 2026-05-21)

| Item | Standard (target) | Live state |
|------|-------------------|------------|
| `set_name` 8 strings | §3.1 | ✅ conforms |
| `master_card_catalog` editions | full canonical (637 cards / 1,354 rows) | ✅ audited complete |
| grade FK on `pop_reports`/`sold_listings` | hard FK | ✅ in place |
| `sold_listings.grade` | text raw_label | ✅ migrated |
| `card_number` bare | §3.2 | ⏳ catalog stores `#NN` (1,354/1,354) — **strip-`#` migration pending** |
| variant SPLIT | `edition` + `print_variant` | ✅ done 2026-05-21 — split live + parity-verified; legacy `variant` retained (trigger-bridged) pending consumer migration; 1 dedup resolved (TR #5 Holo/Unlimited) |
| `master_set_catalog` | §1 / §3.3 | ⏳ **not built** |
| `grade_reject_log` + staging-filter | §4 | ⏳ **not built** (loaders are all-or-nothing) |
| `ebay_listings` columns/FK | `edition`,`card_number`,`grade_tier` + soft validate | ⏳ missing 3 cols; `grade` is `numeric`; no FK; `grader` has descriptors |
| `pop_reports.card_set` | rename to `set_name` | ⏳ pending |
| `card_id` on observations | FK to `master_card_catalog.id` | ⏳ observations join on text today |
