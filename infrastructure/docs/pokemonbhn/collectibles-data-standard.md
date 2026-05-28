# PokemonBHN — Collectibles Data Standard

**Status: AUTHORITATIVE.** This is the single source of truth for the PokemonBHN data domain —
table/column naming, canonical value formats, identity model, and enforcement rules. It is
written and maintained by Claude Code from the **live `eventhorizon` DB**. Where any other doc
(the `PokemonBHN_*` planning set, the retired `BHN-*` docs) disagrees with this file, **this file
wins**; where this file disagrees with the live DB, **the live DB wins** and this file is corrected.

Last verified against the live DB: **2026-05-27** (see [§9 Conformance status](#9-conformance-status)).

**Session Start Protocol:** At the start of any Claude Code session touching PokemonBHN data, run the grade catalog verification query in [§9](#9-conformance-status) (`SELECT grader, raw_label FROM master_grade_catalog ORDER BY grader, numeric_grade DESC NULLS LAST`) before writing any grade logic. Expected: 88 rows (CGC 25 / PSA 20 / BGS 21 / SGC 22). If the DB result disagrees with this document, the DB wins — correct this document first.

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

### Tokenized Market Stream — see [§10](#10-tokenized-market-stream)
A parallel fact stream covering NFT-backed graded cards on Courtyard (Polygon) and Collector Crypt (Solana). Three observation tables plus one cross-market signal table — built day-one compliant with this standard. Same identity model, same grade vocabulary, same enforcement tier (soft) as `ebay_listings`.

### Dimensions — authorities (facts conform to these)
| Table | Authority for |
|-------|---------------|
| `master_card_catalog` (+ `card_catalog` compat view) | which cards exist (card identity) |
| `master_grade_catalog` | valid grades per grader |
| `master_grading_criteria_catalog` | grading criteria + qualifiers |
| `master_set_catalog` | sets: year, era, legal editions, count, PSA heading mapping (8 sets; `master_card_catalog.set_name` FK-bound) |

### Derived dimensions — see [§11](#11-seller-profile-dimension)
Unlike the `master_*` authorities (externally curated), derived dimensions are *populated by aggregating observations*. They are not "truth lists"; they are *summaries the observations have already shown*.

| Table | Captures |
|-------|----------|
| `seller_profiles` | cross-platform seller dimension - per-seller metrics rolled up from `ebay_listings`, `courtyard_listings`, `courtyard_sales`, `collector_crypt_sales` |

### Control
| Table | Role |
|-------|------|
| `ebay_watchlist` | sniper buy-criteria (config, not observation) |

### Three concepts that must never fuse
| Concept | Identifies | Lives where | Cardinality |
|---------|-----------|-------------|-------------|
| `card_id` | a card-variant ("this kind of card exists") | `master_card_catalog.id` (serial PK) | 1,354 |
| `cert_number` | one physical graded slab | `ebay_listings.cert_number`, `sold_listings.cert_number` (added 2026-05-22); other observation streams pending | thousands+ |
| `card_number` | within-set number (a **field**, not a key) | all tables | repeats per set |

Observations should resolve to `card_id` (the dumb surrogate). Do **not** encode meaning into the
key (no smart keys); the **`card_code`** (e.g. `BST-004-1E`) and derived **`slab_code`** (e.g.
`BST-004-1E-PSA-10`) are human-readable identifiers **for display only** — they are never used as
join keys. See [§2.1](#21-card_code--display-identifier) and [§2.2](#22-slab_code--derived-identifier).

---

## 2. Authorities & keys

- **`card_id`** = `master_card_catalog.id` — existing serial PK. Use it; never mint a parallel key.
  (Internal objects `card_catalog_id_seq` / `card_catalog_pkey` retain pre-rename names; harmless.)
- Card identity is the composite **`(set_name, card_number, edition, print_variant)`**, surfaced as `card_id`.
- `card_number` alone is **not** unique — a `4` exists in every set.

### 2.1 `card_code` — display identifier

A stored human-readable label on every `master_card_catalog` row. Lives at
`master_card_catalog.card_code` (TEXT, UNIQUE), added 2026-05-27. **Display / label only —
never use as a join key.** All joins remain on `card_id` (integer).

Format: `SET_CODE-NNN-EDITION_CODE[-VARIANT_CODE]`

- `SET_CODE` — 3 letters per set (column `master_set_catalog.set_code`, UNIQUE):
  `BST` Base Set · `FSL` Fossil · `JGL` Jungle · `TRK` Team Rocket · `GYH` Gym Heroes ·
  `GYC` Gym Challenge · `WSP` Wizards Black Star Promos · `BOG` Best of Game.
- `NNN` — `card_number` zero-padded to 3 digits (`4 → 004`, `132 → 132`).
- `EDITION_CODE` — `1E` 1st Edition · `SH` Shadowless · `UN` Unlimited · `NA` N/A (promos).
- `VARIANT_CODE` (optional, omitted when `print_variant='Standard'`):
  `HOL` Holo · `ERR` Error · `NOS` No Symbol · `WST` W Stamp · `WIN` Winner · `JMB` Jumbo ·
  `PRE` Prerelease · `GLB` Gold Border · `RCK` Red Cheeks · `WBM` WB Movie · `NTP` Nintendo Power ·
  `WTC` WOTC · `C99` 1999-2000 Copyright.

Examples: `BST-004-1E` (Base Set #4 Charizard 1st Edition), `BST-058-1E-ERR` (Base Set #58 Potion
1st Edition Error print), `TRK-004-UN` (Team Rocket #4 Dark Charizard Unlimited), `BOG-001-NA-WIN`
(Best of Game #1 Winner).

The full populate logic lives in [`sql/card-code-system.sql`](../../../sql/card-code-system.sql).
Future sets (Neo Genesis etc.) get their own 3-letter `set_code` when added to `master_set_catalog`.

### 2.2 `slab_code` — derived identifier

Identifies one **graded** card variant — `pbds_code` + grader + grade. **Never stored** —
always derived on demand via `slab_code(p_card_code, p_grader, p_grade)` (PL/pgSQL function,
`STABLE`, granted to `n8n_user`, `log_shipper`, `ehuser`, `agent_reader`).

Format: `[PBDS_CODE]-[GRADER+GRADE]` — **no separator between grader and grade** (locked 2026-05-27):

```
TRK014-2000-1E-HOL-PSA10      (not PSA-10)
TRK014-2000-1E-HOL-CGC9.5     (not CGC-9.5)
TRK014-2000-1E-HOL-RAW        (ungraded — always RAW, never NULL)
```

- Grader: `PSA` · `CGC` · `BGS` · `SGC` (codes only per [§3.4](#34-grader--codes-only)).
- Grade: `numeric_grade` from `master_grade_catalog`, formatted without trailing `.0`
  (`10` not `10.0`; `9.5` stays `9.5`). Returns `RAW` when grader or grade is NULL.
  Returns NULL if the `(grader, grade)` pair doesn't resolve in the catalog.
- Note: distinct raw_labels with the same `numeric_grade` collapse — e.g. CGC `Gem Mint 10`
  and `Pristine 10` both yield `…-CGC10`. The slab_code is a comparison key for
  cross-platform overlap; if the tier distinction matters, use the raw_label directly.

Examples: `BST004-1999-1E-PSA10` · `BST004-1999-SH-CGC10` · `TRK004-2000-1E-BGS9.5` · `TRK014-2000-1E-HOL-RAW`.

Used for HORIZON alert payloads and arbitrage signal display — see
[`tokenized_arbitrage_signals.card_code`](#106-tokenized_arbitrage_signals--signal-table) (added
2026-05-27 for in-row labelling; the slab_code itself is composed at alert time).

### 2.3 `bhn_slab_id` — unique physical card identifier

A **15-character randomly generated alphanumeric** identifier (A–Z, 0–9) assigned once per unique
`slab_code`. Stored on `ebay_transactions` and `ebay_asks`; NULL for ungraded rows.

- Assigned at first observation of a `slab_code` — the same physical slab always gets the same
  `bhn_slab_id` across relists or platform transfers.
- Used to resolve ambiguous grades (e.g. CGC 10 → Pristine vs Gem Mint) when the `grade_label`
  has been captured from at least one listing title.
- Never blocks a row from loading — informational / display aid only; absence of a `bhn_slab_id`
  is valid and expected for ungraded cards and early-backfill rows.
- Generation: `crypto.randomBytes(12).toString('base64').toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,15)`
  (re-draw if collision with existing rows — expected frequency: negligible at current cardinality).

Status: ⏳ not yet built — defined 2026-05-27. See [§9 Open items](#open-items).

---

## 3. Canonical value formats (verified 2026-05-27)

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
  names everywhere. **Known drift to fix:**
  - `pop_reports` uses `card_set` (should be `set_name`).
  - `sold_listings.seller` (legacy) vs `ebay_listings.seller_username` vs `seller_profiles.seller_username` — same concept, three names. Standard target: `seller_username` everywhere; `sold_listings` rename pending.
  - `seller_profiles.seller_feedback_score` (INT) vs `ebay_listings.seller_feedback` (INTEGER) — same concept (feedback count), two names. Standard target: pick one; rename pending.

### 3.8 The three-column grade system

Every graded-card observation row carries three grade-related fields:

| Column | Stored? | Definition |
|--------|---------|------------|
| `grade` | **YES** | Verbatim raw_label — FK-enforced against `master_grade_catalog` |
| `grade_label` | **YES** | Tier name only, parsed from listing title — e.g. `Gem Mint`, `Pristine`, `NM-MT` |
| `grade_numeric` | **NO** | Numeric value — derived via JOIN to `master_grade_catalog`, never stored on facts |

**`grade_label` rules:**
- Populated when the seller includes the tier name in the listing title.
- NULL/empty when the title contains only a bare number (e.g. `PSA 9`).
- Used to resolve ambiguous grades — primarily CGC 10, where `Pristine 10` and `Gem Mint 10`
  are distinct raw_labels with different market values.
- Never blocks a row from loading — informational / display aid only.

**`grade_numeric` rules:**
- **NEVER stored** on any fact table.
- Always derived: `JOIN master_grade_catalog ON (grader, raw_label)`.
- Used for sorting, cross-grader comparison, and HORIZON calculations.

**Ambiguity resolution:**
- CGC bare `10` with no tier label in the listing title → `grade_reject_log` (cannot determine
  `Pristine 10` vs `Gem Mint 10` without seeing the physical slab). Do not guess.
- All other graders: bare numeric resolves unambiguously from the catalog.

---

## 4. Enforcement tiers (by table role)

| Table | Grade enforcement |
|-------|-------------------|
| `pop_reports`, `sold_listings` | **hard FK** `(grader, grade) → master_grade_catalog(grader, raw_label)` |
| `ebay_listings`, `courtyard_listings`, `courtyard_sales`, `collector_crypt_sales` | **soft validate-and-log** (live high-churn feeds) |
| `tokenized_arbitrage_signals` | **soft** (signal table; references the observation streams above) |

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

## 9. Conformance status (target vs. live, 2026-05-27)

| Item | Standard (target) | Live state |
|------|-------------------|------------|
| `set_name` 8 strings | §3.1 | ✅ conforms |
| `master_card_catalog` editions | full canonical (637 cards / 1,354 rows) | ✅ audited complete |
| grade FK on `pop_reports`/`sold_listings` | hard FK | ✅ in place |
| `sold_listings.grade` | text raw_label | ✅ migrated |
| `card_number` bare | §3.2 | ✅ migrated 2026-05-27 — `#` stripped from all 1,354 catalog rows via `sql/card-id-resolver.sql` |
| variant SPLIT | `edition` + `print_variant` | ✅ done 2026-05-21 — split live + parity-verified; 1 dedup resolved (TR #5 Holo/Unlimited). Legacy `variant` column + `mcc_variant_split_trg` trigger + `idx_card_catalog_unique` dropped 2026-05-27 via `sql/migrations/2026-05-27-drop-mcc-variant.sql` (consumer audit: zero readers across n8n-workflows/, scripts/, infrastructure/scrapers/). |
| `master_set_catalog` | §1 / §3.3 | ✅ built 2026-05-21 — 8 sets, legal_editions + PSA headings; `set_name` FK-bound; DDL in `sql/` |
| `grade_reject_log` + staging-filter | §4 | ⏳ **not built** (loaders are all-or-nothing) |
| `ebay_listings` columns/FK | `edition`,`card_number`,`grade_tier` + soft validate | ⏳ missing 3 cols; `grade` is `numeric`; no FK; `grader` has descriptors |
| `pop_reports.card_set` | rename to `set_name` | ⏳ pending |
| `card_id` on observations | FK to `master_card_catalog.id` | ✅ added 2026-05-27 to `ebay_listings`, `sold_listings`, `courtyard_listings`, `courtyard_sales`, `collector_crypt_sales`, `tokenized_arbitrage_signals` via `sql/card-id-resolver.sql`. `resolve_card_id()` PL/pgSQL function granted to `n8n_user`/`log_shipper`/`ehuser`. Backfill: sold_listings 93.5% resolved (609/651); ebay_listings 0% — known data-quality issue (set_name='Base' drift, card_name NULL) tracked separately. |
| `card_code` display identifier | §2.1 | ✅ added 2026-05-27 — `set_code` on `master_set_catalog` (8 codes, UNIQUE) + `card_code` on `master_card_catalog` (1,354/1,354 populated, UNIQUE) via `sql/card-code-system.sql` |
| `slab_code()` derived identifier | §2.2 | ✅ added 2026-05-27 — PL/pgSQL function `slab_code(card_code, grader, grade)` granted to `n8n_user`/`log_shipper`/`ehuser`/`agent_reader`; never stored |
| `courtyard_listings`, `courtyard_sales`, `collector_crypt_sales`, `tokenized_arbitrage_signals` | day-one compliant per [§10](#10-tokenized-market-stream) | ✅ created 2026-05-22 with edition+print_variant NOT NULL, grader/edition/print_variant CHECK, grade TEXT, sold_price separate from listed_price |
| `seller_profiles` | cross-platform seller dimension per [§11](#11-seller-profile-dimension) | ✅ created 2026-05-22; UNIQUE (seller_username, platform); self-ref `linked_seller_id` (INT references BIGINT - implicit cast at FK lookup, low-impact precision drift) |
| `ebay_listings` enrichment cols | observation-history + slab-identity + demand columns | ✅ added 2026-05-22: `auction_end_time`, `first_seen_at`, `last_seen_at`, `original_item_id`, `relist_count INT DEFAULT 0`, `original_listed_at`, `cert_number`, `location`, `watchers`. `obo_min_price` preserved as legacy NUMERIC (operator spec asked for DECIMAL(10,2); operationally equivalent; rewrite scheduled separately) |
| `sold_listings` enrichment cols | same set as ebay_listings | ✅ added 2026-05-22: all 10 columns including a fresh `obo_min_price DECIMAL(10,2)` |
| `slab_code` format | §2.2 — `[PBDS]-[GRADER+GRADE]`, no separator between grader and grade | ✅ locked 2026-05-27 |
| `slab_code` ungraded | §2.2 — `-RAW` suffix, never NULL | ✅ locked 2026-05-27 |
| `bhn_slab_id` | §2.3 — 15-char random alphanumeric per unique slab_code — stored on `ebay_transactions` + `ebay_asks`, NULL for ungraded | ⏳ not yet built |
| `grade_label` column | §3.8 — tier name parsed from title, nullable, never blocks load | ⏳ not yet on `ebay_transactions` |
| `currency` standardization | `currency TEXT` present on all `_transactions` tables, `USD`/`CAD`/`GBP` | ⏳ `ebay_transactions` has column; other tables pending audit |

### Open items

| # | Item | Notes |
|---|------|-------|
| 1 | `grade_reject_log` + staging-filter | Loaders still all-or-nothing — not yet built |
| 2 | `ebay_listings` columns/FK | Missing `edition`, `card_number`, `grade_tier`; `grade` is NUMERIC; no FK; `grader` has descriptors |
| 3 | `pop_reports.card_set` rename | Should be `set_name` |
| 4 | `sold_listings.seller` rename | Should be `seller_username` |
| 5 | `seller_feedback` name unification | `ebay_listings.seller_feedback` vs `seller_profiles.seller_feedback_score` |
| 6 | `linked_seller_id INT` type drift | Should be `BIGINT`; acceptable until seller count approaches 2.1B |
| 7 | `ebay_listings.obo_min_price` type | Is `NUMERIC` (legacy); target `DECIMAL(10,2)` |
| 8 | `bhn_slab_id` | Defined §2.3 — not yet built; add column to `ebay_transactions` + `ebay_asks` |
| 9 | `grade_label` column | Defined §3.8 — not yet on `ebay_transactions`; scraper will populate |
| 10 | `currency` audit | Confirm `currency TEXT` present and populated on all `_bids`, `_asks`, `_transactions` tables |

---

## 10. Tokenized Market Stream

A parallel fact stream alongside the Big 3, capturing the **NFT-backed graded-card market**: cards minted as tokens on Courtyard (Polygon) and Collector Crypt (Solana) that represent real physical slabs held in custody. The same graded card can appear simultaneously across the physical (eBay) and tokenized markets — that overlap is exactly what the cross-market arbitrage signal table is built to surface.

Schema lives at [`sql/tokenized-market-schema.sql`](../../../sql/tokenized-market-schema.sql); applied to live `eventhorizon` on 2026-05-22.

### 10.1 Tables

| Table | Captures | Lifecycle | Idempotency |
|-------|----------|-----------|-------------|
| `courtyard_listings` | active NFT listings on Courtyard (Polygon) | mutable (UPDATE allowed) | `item_id UNIQUE` |
| `courtyard_sales` | completed NFT sales on Courtyard (Polygon) | immutable | `item_id UNIQUE` |
| `collector_crypt_sales` | completed sales on Collector Crypt (Solana) | immutable | `item_id UNIQUE` |
| `tokenized_arbitrage_signals` | cross-market opportunity flags | mutable (review/action flags) | `id BIGSERIAL` |

### 10.2 Shape: mirror of `ebay_listings` + standard-required + tokenized additions

The three observation tables (`courtyard_listings`, `courtyard_sales`, `collector_crypt_sales`) have **identical** column shape — 40 columns each, in this order:

1. **`ebay_listings` mirror block (27 columns)** — `id`, `item_id`, `title`, `card_name`, `grader`, `grade`, `listed_price`, `shipping`, `seller_username`, `seller_feedback`, `seller_feedback_pct`, `listing_url`, `image_url`, `condition`, `item_creation_date`, `returns_accepted`, `listed_at`, `created_at`, `current_bid`, `bid_count`, `currency`, `transaction_type`, `obo_available`, `obo_min_price`, `set_name`, `language`, `item_url`.

   Mirror is column-for-column, types-and-order-exact, with **one type correction**: `grade` is `TEXT` (not `NUMERIC` — `ebay_listings`'s `numeric` is acknowledged drift per [§9](#9-conformance-status), and the new tables converge to the standard).

   Mirrored-but-always-NULL on tokenized rows (kept for shape parity, not data):
   - `shipping` — tokenized cards don't ship per transaction
   - `bid_count`, `current_bid`, `obo_*` — no eBay-style auctions / best-offer on tokenized
   - `seller_feedback`, `seller_feedback_pct` — no reputation system
   - `returns_accepted` — N/A

2. **Standard-required columns missing from `ebay_listings`'s current drift (4 columns)** — `card_number TEXT`, `edition TEXT NOT NULL DEFAULT 'N/A'`, `print_variant TEXT NOT NULL DEFAULT 'Standard'`, `sold_price NUMERIC`.

   `edition` and `print_variant` enforce the [§3.3](#33-variant--split-into-edition--print_variant) vocab via CHECK. `sold_price` keeps listed/sold separation per [§3.6](#36-money).

3. **Tokenized-only additions (9 columns)** — `platform TEXT NOT NULL`, `blockchain TEXT NOT NULL`, `transaction_hash`, `sale_type TEXT` (CHECK ∈ `{peer_to_peer, buyback, gacha}`), `seller_address`, `buyer_address`, `sol_price DECIMAL(20,9)` (Solana native units), `sol_usd_rate DECIMAL(10,2)`, `nft_contract`.

   Per-table CHECK pins `(platform, blockchain)`:
   - `courtyard_listings` / `courtyard_sales`: `platform='courtyard'`, `blockchain='polygon'`
   - `collector_crypt_sales`: `platform='collector_crypt'`, `blockchain='solana'`

### 10.3 Grader codes (CHECK-enforced on all four tables)

`grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')` — no descriptors per [§3.4](#34-grader--codes-only). This is **stricter** than `ebay_listings` today (which still admits descriptors); the new tables don't inherit that drift.

### 10.4 Grade enforcement: soft validate, no FK

`grade` is `TEXT` (verbatim raw_label) but there is **no FK** to `master_grade_catalog`. Same tier as `ebay_listings` — high-churn ingestion via the Courtyard / Collector Crypt scrapers should not roll back batches on a single unknown label. Loaders are expected to validate against `master_grade_catalog` and divert unknowns to the (still-pending) `grade_reject_log`. Raw / ungraded rows: `grade = NULL`.

### 10.5 Money model

- `listed_price` — the ask (populated for listings; populated as pre-sale ask on sales if known).
- `sold_price` — the realized USD-pegged sale price (populated on sales; NULL on listings).
- `sol_price` + `sol_usd_rate` — Solana sales record native-currency view; FX captured at sale time. USD-pegged value (`sold_price`) is what's used for cross-market comparison.
- Always: NULL means absent, 0 means free / zero. Never silently zeroed.

### 10.6 `tokenized_arbitrage_signals` — signal table

Cross-market opportunity flags. Schema:

```
id                  BIGSERIAL PK
detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
card_name           TEXT NOT NULL
set_name            TEXT
grader, grade, edition, print_variant   -- standard-vocab CHECK
ebay_item_id        TEXT                -- soft ref to ebay_listings.item_id
ebay_listed_price   DECIMAL(10,2)
ebay_90d_avg        DECIMAL(10,2)
courtyard_ask       DECIMAL(10,2)
collector_crypt_ask DECIMAL(10,2)
tokenized_90d_avg   DECIMAL(10,2)
buyback_floor       DECIMAL(10,2)
spread_pct          DECIMAL(6,2)
estimated_profit    DECIMAL(10,2)
signal_strength     TEXT CHECK IN ('weak','moderate','strong','critical')
reviewed, actioned  BOOLEAN DEFAULT FALSE
notes               TEXT
expires_at          TIMESTAMPTZ
raw_payload         JSONB
```

`ebay_item_id` is a **soft reference** (not a hard FK) — `ebay_listings` is high-churn and rows may be purged before a signal expires, so the signal row needs to survive that.

### 10.7 Grants (per role)

| Role | Permissions |
|------|-------------|
| `log_shipper` | `INSERT` on all 3 observation tables; `UPDATE` on `courtyard_listings` only (sales are immutable) |
| `n8n_user` | `INSERT, UPDATE` on `tokenized_arbitrage_signals` |
| `agent_reader` | `SELECT` on all 4 tables |
| `grafana_reader` | `SELECT` on all 4 tables |
| `ehuser` | `SELECT` on all 4 tables |

Sequence `USAGE` granted to writers as needed for the `SERIAL` / `BIGSERIAL` defaults.

### 10.8 Relationship to `seller_profiles`

Tokenized observation tables carry `seller_username` (mirror column from `ebay_listings`) and `seller_address` (the wallet address — a tokenized-specific addition). Neither is FK-bound to `seller_profiles`, but both are *expected* to map to it for cross-table joins:

- Courtyard rows → `seller_profiles WHERE platform='courtyard'` on `seller_username`
- Collector Crypt rows → `seller_profiles WHERE platform='collector_crypt'` on `seller_username` (which on CC is operationally the wallet address)

A single real-world seller appearing across multiple platforms is asserted via `seller_profiles.linked_seller_id` (operator/HORIZON, not auto-derived). See [§11](#11-seller-profile-dimension).

---

## 11. Seller Profile Dimension

A **derived** dimension — one row per `(seller_username, platform)` — populated by aggregating signals from the observation streams. Unlike the `master_*` authorities (externally curated truth lists), `seller_profiles` is a *summary the observations have already shown*: how many listings has this seller posted, how many have sold, what's their sell-through rate, are they a dealer, are they flagged.

Schema lives at [`sql/seller-profiles-schema.sql`](../../../sql/seller-profiles-schema.sql); applied to live `eventhorizon` on 2026-05-22.

### 11.1 Identity & uniqueness

| Aspect | Value |
|--------|-------|
| Primary key | `id BIGSERIAL` |
| Natural key | `UNIQUE (seller_username, platform)` |
| Platform vocab | `platform CHECK IN ('ebay','courtyard','collector_crypt')` |
| Cross-platform linking | `linked_seller_id INT REFERENCES seller_profiles(id)` — self-ref pointer asserting "the seller with this id and the seller in *this* row are the same real-world person operating under different usernames" |

> ⚠️ **Type drift:** `linked_seller_id INT` references `id BIGSERIAL` (== `BIGINT`). Postgres accepts this FK with an implicit cast at lookup time. Promote `linked_seller_id` to `BIGINT` later if seller count ever approaches 2.1B (almost certainly never). Flagged in §9.

### 11.2 Metrics columns

| Column | Type | Captures |
|--------|------|----------|
| `seller_feedback_score` | `INT` | eBay-style feedback count (when applicable) |
| `seller_feedback_pct` | `DECIMAL(5,2)` | positive-feedback % |
| `total_listings_seen` | `INT DEFAULT 0` | lifetime listings observed |
| `total_sold` | `INT DEFAULT 0` | lifetime sales observed |
| `sell_through_rate` | `DECIMAL(5,2)` | `total_sold / total_listings_seen` (as %) |
| `avg_days_to_sell` | `DECIMAL(6,1)` | mean time-on-market for sold inventory |
| `avg_price_cut_pct` | `DECIMAL(5,2)` | mean price reduction before sale |
| `relist_frequency` | `DECIMAL(5,2)` | how often the same card is relisted (per period) |
| `active_listings` | `INT DEFAULT 0` | current live listings |
| `active_listings_value` | `DECIMAL(10,2)` | sum of current asks |
| `avg_listing_age_days` | `DECIMAL(6,1)` | mean age of active inventory |
| `last_seen_at`, `first_seen_at` | `TIMESTAMPTZ` | first/last observation window |
| `is_dealer` | `BOOLEAN DEFAULT FALSE` | operator/HORIZON-asserted: this seller operates as a professional reseller |
| `is_flagged` | `BOOLEAN DEFAULT FALSE` | operator/HORIZON-asserted: seller is under review (suspect pricing, suspect grading, prior bad transaction) |
| `notes` | `TEXT` | free-form operator notes |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | last refresh; writers must update on each enrichment |

### 11.3 Grants

| Role | Permissions |
|------|-------------|
| `log_shipper` | `INSERT, UPDATE` (scraper-side enrichment as new sellers / observations appear) |
| `n8n_user` | `INSERT, UPDATE` (HORIZON workflow enrichment — feature flags `is_dealer`, `is_flagged`, signal-weighting metrics) |
| `agent_reader` | `SELECT` |
| `grafana_reader` | `SELECT` |
| `ehuser` | `SELECT` |

### 11.4 Naming drift to converge

The new dimension surfaced a long-standing naming drift across the observation streams — same concepts under different column names. Not fixed in this batch; tracked here so it doesn't fall through.

| Concept | Current names | Target |
|---------|---------------|--------|
| Seller username | `ebay_listings.seller_username`, `sold_listings.seller`, `seller_profiles.seller_username`, tokenized tables `seller_username` | `seller_username` everywhere — `sold_listings.seller` rename pending |
| Seller feedback count | `ebay_listings.seller_feedback`, `seller_profiles.seller_feedback_score` | pick one — `seller_feedback_score` is the more descriptive choice |

---

## 12. Market Data Standard v2 — uniform `[market]_{bids,asks,transactions}` naming (2026-05-27)

Authoritative spec text: `infrastructure/docs/BHN session updates/BHN-SESSION-HANDOFF/BHN-MARKET-DATA-STANDARD-PART{1,2,3}-*.txt` (v2). §12 and §13 of this doc are the steady-state shape; the three Part files are the change-log.

### 12.1 Renames (in-place ALTER, then RENAME TO — preserves all data)

| Old name | New name |
|---|---|
| `ebay_listings` | `ebay_asks` |
| `sold_listings` | `ebay_transactions` |
| `courtyard_listings` | `courtyard_asks` |
| `courtyard_sales` | `courtyard_transactions` |
| `collector_crypt_sales` | `collector_crypt_transactions` |

Back-compat views (same old names) point at the new tables and stay alive until every n8n workflow + collector script has been migrated and re-tested. View drops are operator-gated, one at a time, after `grep -r "<old_name>" n8n-workflows/ scripts/` is empty for that name. Precedent: `card_catalog` view kept after `master_card_catalog` rename.

### 12.2 New tables

| Table | Purpose |
|---|---|
| `ebay_bids` | Best Offer / OBO offers on YOUR eBay listings (Trading API). Cannot see offers on other sellers' listings — sparsely populated. |
| `courtyard_bids` | Offers on Courtyard tokens (OpenSea Offers API). Full coverage. |
| `collector_crypt_bids` | Bids on CC tokens (Magic Eden Bids API). Full coverage. |
| `collector_crypt_asks` | CC sell listings (Magic Eden Listings API). New — the 2026-05-22 schema only covered sales. |
| `order_price_history` | Bid + ask price-change log across all markets. Polled comparison of last_seen vs current. |
| `fee_schedule` | Platform fee reference table — source of truth for every cost estimate. See §13. |
| `arbitrage_positions` | Full trade lifecycle: signal → buy → list → sell, with three-way (market / est / actual) fee accounting and P&L. |

### 12.3 Universal columns

Present on EVERY `_bids`, `_asks`, `_transactions` table:

`id` (PK), `card_id` FK, `card_code`, `card_name`, `set_name`, `card_number`, `grader`, `grade`, `edition` (NOT NULL DEFAULT 'N/A'), `print_variant` (NOT NULL DEFAULT 'Standard'), `platform`, `currency`, `created_at`, `raw_payload` (JSONB).

`card_id` is NULLABLE — unresolved rows still insert; they're excluded from arbitrage joins, not from the table.

### 12.4 `_asks` outcome vocabulary (per-market CHECK)

| Market | Allowed outcomes |
|---|---|
| `ebay_asks` | `active`, `sold_full_price`, `sold_auction`, `sold_obo`, `expired_no_bids`, `expired_with_bids`, `cancelled_seller`, `relisted`, `ended_other` |
| `courtyard_asks` | `active`, `sold`, `delisted`, `price_reduced`, `expired` |
| `collector_crypt_asks` | `active`, `sold`, `delisted`, `price_reduced`, `expired`, `buyback` |

### 12.5 `_transactions.sale_type` vocabulary (uniform across markets)

`fixed_price`, `auction`, `offer_accepted`, `buyback` (CC only), `peer_to_peer`. The pre-v2 CC/Courtyard constraint (`peer_to_peer`/`buyback`/`gacha`) was broadened during the migration.

### 12.6 `_bids` vocabulary

`offer_type ∈ {individual, collection, trait, obo}`. `status ∈ {open, accepted, declined, expired, cancelled}`.

eBay can only populate `ebay_bids` from YOUR own listings (Trading API limitation) — this table will be sparse. OpenSea and Magic Eden expose full bid feeds.

---

## 13. Fee Schedule & Cost Estimation (2026-05-27)

### 13.1 `fee_schedule` table

Every cost estimate in the system reads from `fee_schedule` — never hardcoded. When a platform changes rates, INSERT a new row with a later `effective_date`; queries filter on `effective_date` so historical rates remain queryable.

`fee_type` controlled vocab: `platform_pct`, `platform_flat`, `payment_pct`, `payment_flat`, `royalty_pct`, `shipping_flat`, `shipping_pct`, `authentication_flat`, `redemption_flat`, `tokenization_flat`, `gas_flat`, `tax_pct`.

`tier` (TEXT, nullable) — added 2026-05-27 in step 06 to disambiguate mutually-exclusive eBay seller plans. Controlled vocab `{all, non_store, basic_store, premium_store, anchor_store}`. Tagging on the live seed:

| tier | eBay rows |
|---|---|
| `non_store` | `Final Value Fee` (13.25%), `FVF Above $7,500` (2.35%) |
| `basic_store` | `FVF Basic Store` (12.35%), `FVF Basic Above $2,500` (2.35%) |
| `all` | `Payment Processing`, `Per-Order Fee` (both), `Authenticity Guarantee`, `Shipping (graded card)`, `FVF 50% Promo` |
| `NULL` | every Courtyard / Collector Crypt row (n/a — non-tiered markets) |

`estimate_trade_costs()` filters `(tier IS NULL OR tier = 'all' OR tier = p_ebay_tier)` so only one FVF rate ever participates in a single call. Reserved values `premium_store` / `anchor_store` are placeholders for future eBay plans — add seed rows when needed; no schema change required.

Seed rows verified 2026-05-27 (Courtyard 6, Collector Crypt 3, eBay 10) — see `sql/market-data-standard-03-fee-schedule-seed.sql`. Each row carries a `verified_source` URL/note. Promotional rows (e.g. expired eBay FVF 50% promo) stay in the table for historical reference.

### 13.2 `estimate_trade_costs()` function

```sql
SELECT * FROM estimate_trade_costs(
    p_buy_market   := 'courtyard',
    p_sell_market  := 'ebay',
    p_buy_price    := 800,
    p_sell_price   := 1100,
    p_direction    := 'courtyard_to_ebay',
    p_ebay_tier    := 'non_store'   -- optional; default 'non_store'
);
```

Returns: `buy_fees_est`, `sell_fees_est`, `shipping_est`, `redemption_est`, `tokenization_est`, `gas_est`, `total_costs_est`, `net_profit_est`, `roi_est_pct`, `is_profitable` (vs default $25 minimum threshold).

The 6th parameter `p_ebay_tier` was added in step 06 to model eBay's mutually-exclusive FVF tiers. Defaults to `non_store` (the operator's current plan); pass `basic_store` to project costs as if upgraded. Non-eBay sell markets ignore the tier filter (their fee rows are `tier IS NULL`). Verified worked example (Part 2 §4, PSA-10 $800→$1,100):

| tier | sell_fees | net_profit | ROI | profitable |
|---|---|---|---|---|
| `non_store` | $178.05 | +$111.94 | 13.99% | ✓ |
| `basic_store` | $168.15 | +$121.84 | 15.23% | ✓ |

The spec's "$121 net / 15.2% ROI" target matched `basic_store` exactly — the spec authors implicitly used Basic Store rates. Default `non_store` gives the more conservative projection; if a trade is profitable at `non_store`, it's profitable at any higher tier.

Called by the arbitrage signal generator BEFORE a signal fires. A signal with `is_profitable_est = FALSE` must not produce an alert.

### 13.3 Three-way cost accounting on `arbitrage_positions`

Every cost line item carries three views:

| View | Source | Use |
|---|---|---|
| `market_*` | published rate from `fee_schedule` | what published fees would have been |
| `est_*` | output of `estimate_trade_costs()` at signal time | pre-trade projection |
| `actual_*` | populated after trade closes | the number that actually mattered |

Deltas (`delta_market_vs_est`, `delta_est_vs_actual`, `delta_market_vs_actual`) feed weekly calibration (Part 3 §4) — drift the seed rates when `delta_est_vs_actual` shows a consistent bias.

### 13.4 Market-rate estimates on observed third-party `_transactions`

For sales you didn't make, actual fees aren't visible — but you can estimate what the seller netted using `fee_schedule`. Populated on every `_transactions` row:

`market_platform_fee_est`, `market_processing_fee_est`, `market_shipping_est`, `market_auth_fee_est`, `market_total_costs_est`, `market_net_to_seller_est`.

Worked example: a PSA-10 selling for $1,000 on eBay vs $900 on Courtyard — the Courtyard seller nets ~$87 more despite the $100 lower headline price. This is the structural spread the arbitrage signal exploits.

### 13.5 Migration files

| Step | File |
|---|---|
| 01 — renames + column extensions | `sql/market-data-standard-01-renames.sql` |
| 02 — new tables | `sql/market-data-standard-02-new-tables.sql` |
| 03 — fee_schedule seed | `sql/market-data-standard-03-fee-schedule-seed.sql` |
| 04 — `estimate_trade_costs()` + signal extension | `sql/market-data-standard-04-estimate-fn.sql` |
| 05 — back-compat views | `sql/market-data-standard-05-backcompat-views.sql` |
| 06 — `fee_schedule.tier` + tier-aware function | `sql/market-data-standard-06-fee-tier-fix.sql` |

Out of scope for this batch: n8n workflow migration off the back-compat views, HORIZON SMS query wiring (Part 2 §4, Part 3 §5), calibration cron (Part 3 §4). Each is gated on operator decisions and goes in a follow-up.
