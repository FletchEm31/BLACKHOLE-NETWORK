# Courtyard Sales Data Audit — 2026-06-03

## Summary

courtyard_sales has 546 rows loaded in a single backfill window (May 27–28, 2026 UTC). The n8n real-time collector is active but has not added rows since the initial load — likely because OpenSea API credentials need to be verified in n8n.

---

## Field Population

| Column | Populated | % | Note |
|---|---|---|---|
| item_id | 546 | 100% | NFT token ID — unique key |
| grader | 546 | 100% | All rows have a grader trait |
| grade | 546 | 100% | Verbatim raw_label from traits |
| sold_price | 546 | 100% | USDC amount |
| transaction_hash | 546 | 100% | Polygon tx hash |
| card_number | 536 | 98.2% | Extracted from Card Number trait |
| set_name | 13 | 2.4% | Only WOTC matches (correct — see below) |
| card_id | 13 | 2.4% | Only resolves where set_name matched |
| **language** | **0** | **0%** | **Gap: trait exists but not in INSERT SQL** |
| **cert_number** | **0** | **0%** | **Gap: Serial trait exists but not in INSERT SQL** |

**Price range:** $5.20 – $4,694.40 (avg $82.60)

---

## Set Breakdown — WOTC vs Non-WOTC

| set_name | count |
|---|---|
| NULL (non-WOTC) | 533 |
| Base Set | 8 |
| Jungle | 3 |
| Fossil | 2 |

**97.6% of Courtyard sales are non-WOTC modern cards.** This is expected and correct — Courtyard is a general graded-card NFT platform, not Pokemon-specific. The n8n normalizer correctly returns NULL set_name for sets not in the 8-set canonical list.

---

## Modern Set Fragmentation (should we catalog them?)

**Short answer: No.**

The 533 non-WOTC rows span **252 distinct sets** across both Pokemon modern sets and non-Pokemon collectibles:

| Set (from traits) | Language | Sales | Avg $ |
|---|---|---|---|
| Topps | English | 34 | $49 |
| Pokémon Meg EN-Mega Evolution | English | 33 | $28 |
| Pokémon M2-Inferno X | Japanese | 12 | $46 |
| Pokémon Pre EN-Prismatic Evolutions | English | 9 | $18 |
| Panini Prizm | English | 8 | $30 |
| Pokémon Sv8a-Terastal Fest EX | Japanese | 7 | $28 |
| Pokémon Swsh Black Star Promo | English | 7 | $387 |
| ... 245 more sets ... | | | |

Average: **2.1 rows per set.** No single non-WOTC set has more than 34 rows. The data includes non-Pokemon items (Topps, Panini, Bowman Draft). **Building a modern card catalog to cover these 533 rows would cost more in schema/scraping work than the data density justifies.** Recommendation: filter non-WOTC rows out of arbitrage signals, leave catalog expansion for a dedicated future decision.

---

## n8n INSERT SQL Gap

Both `courtyard-bhn-sales-collector.json` and `courtyard-bhn-listings-collector.json` INSERT SQL do **not** include `language` or `cert_number` columns, despite the Code node extracting both from OpenSea traits.

**Fix needed (requires Fletch n8n approval):**
Add `language` and `cert_number` to the INSERT column list and corresponding `$N` parameter bindings in both workflows.

**Backfill for existing 546 rows:** `sql/migrations/2026-06-03-courtyard-backfill-language-cert.sql` — ready to run pending Fletch approval.

---

## Action Items

| Item | Priority | Status |
|---|---|---|
| Run backfill SQL (language + cert_number) | Medium | Needs Fletch approval |
| Fix n8n INSERT to include language + cert_number | Medium | Needs Fletch approval |
| Verify n8n OpenSea API key is active in credentials | High | Needs Fletch check — no new rows since May 28 |
| Decide on non-WOTC catalog expansion | Low | Recommendation: defer indefinitely |
