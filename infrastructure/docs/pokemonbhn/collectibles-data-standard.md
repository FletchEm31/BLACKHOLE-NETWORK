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

### Tokenized Market Stream — see [§10](#10-tokenized-market-stream)
A parallel fact stream covering NFT-backed graded cards on Courtyard (Polygon) and Collector Crypt (Solana). Three observation tables plus one cross-market signal table — built day-one compliant with this standard. Same identity model, same grade vocabulary, same enforcement tier (soft) as `ebay_listings`.

### Dimensions — authorities (facts conform to these)
| Table | Authority for |
|-------|---------------|
| `master_card_catalog` (+ `card_catalog` compat view) | which cards exist (card identity) |
| `master_grade_catalog` | valid grades per grader |
| `master_grading_criteria_catalog` | grading criteria + qualifiers |
| `master_set_catalog` | sets: year, era, legal editions, count, PSA heading mapping (8 sets; `master_card_catalog.set_name` FK-bound) |

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

## 9. Conformance status (target vs. live, 2026-05-21)

| Item | Standard (target) | Live state |
|------|-------------------|------------|
| `set_name` 8 strings | §3.1 | ✅ conforms |
| `master_card_catalog` editions | full canonical (637 cards / 1,354 rows) | ✅ audited complete |
| grade FK on `pop_reports`/`sold_listings` | hard FK | ✅ in place |
| `sold_listings.grade` | text raw_label | ✅ migrated |
| `card_number` bare | §3.2 | ⏳ catalog stores `#NN` (1,354/1,354) — **strip-`#` migration pending** |
| variant SPLIT | `edition` + `print_variant` | ✅ done 2026-05-21 — split live + parity-verified; legacy `variant` retained (trigger-bridged) pending consumer migration; 1 dedup resolved (TR #5 Holo/Unlimited) |
| `master_set_catalog` | §1 / §3.3 | ✅ built 2026-05-21 — 8 sets, legal_editions + PSA headings; `set_name` FK-bound; DDL in `sql/` |
| `grade_reject_log` + staging-filter | §4 | ⏳ **not built** (loaders are all-or-nothing) |
| `ebay_listings` columns/FK | `edition`,`card_number`,`grade_tier` + soft validate | ⏳ missing 3 cols; `grade` is `numeric`; no FK; `grader` has descriptors |
| `pop_reports.card_set` | rename to `set_name` | ⏳ pending |
| `card_id` on observations | FK to `master_card_catalog.id` | ⏳ observations join on text today |
| `courtyard_listings`, `courtyard_sales`, `collector_crypt_sales`, `tokenized_arbitrage_signals` | day-one compliant per [§10](#10-tokenized-market-stream) | ✅ created 2026-05-22 with edition+print_variant NOT NULL, grader/edition/print_variant CHECK, grade TEXT, sold_price separate from listed_price |

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
