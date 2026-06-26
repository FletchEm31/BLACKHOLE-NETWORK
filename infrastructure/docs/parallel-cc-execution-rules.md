# Parallel CC Execution Rules

Binding rules for running two Claude Code instances (CC1 + CC2) in parallel
against the BLACKHOLE-NETWORK repo and live infrastructure. Both instances
must read this doc at session start. The operator is the sole coordinator
between instances — neither CC reads the other's transcript directly.

---

## 1. Path ownership (no overlapping writes)

Each CC owns a disjoint set of repo paths and live-infra targets. Cross-path
writes are forbidden without explicit operator handoff.

### CC1 owns
- `n8n-workflows/` (all workflow JSON)
- `sql/` (schema, migrations, grants, verify scripts)
- `infrastructure/docs/pokemonbhn/` (collectibles standard, set docs)
- `infrastructure/services/redis/` and any HORIZON-memory-layer config
- `scripts/operator-pc/` (clone/ingest scripts for collectibles)
- LA hub (`<BHN_WG_LA_IP>`) — Docker stack, PG schema, n8n DB inspection only
  (n8n UI changes are operator-only; CC1 flags and waits)

### CC2 owns
- `scripts/trading/` (trading_core.py, strategies, reconciliation)
- `/etc/bhn-trading/` on NJ (env files, strat envs)
- NJ node (`<BHN_NJ_PUBLIC_IP>:2222`) — systemd units, cron, rules.json
- Frankfurt server-side teardown: WireGuard peer removal on LA,
  MyFamily/Tor cleanup on Hillsboro + NJ
- Trading-related SQL only inside `trading_strategies`, `signals_log`,
  `paper_trades`, `circuit_breaker_log`, `strategy_performance`
  (status flips, pauses, reads — no schema changes without handoff)

### Shared, gated on handoff
- `infrastructure/archive/frankfurt/` and Frankfurt-referenced docs in
  `infrastructure/docs/` — CC2 does server-side cleanup first, signals
  complete; only then may CC1 move docs and commit.
- `infrastructure/docs/bhn-network-data-flow.md` — CC1 strips FRA
  references, but only after CC2 confirms FRA peer removed from `wg0`.

### Forbidden to both unless explicitly tasked
- WireGuard mesh config beyond Frankfurt peer removal
- PostgreSQL core config (`postgresql.conf`, `pg_hba.conf`)
- fail2ban, CrowdSec, Suricata rules
- n8n container restarts or compose changes
- Grafana dashboard JSON (operator-curated)

---

## 2. Coordination protocol

The operator is the only message bus between CC1 and CC2.

- When a CC needs the other to act or finish first, it emits a clearly
  labelled signal line in its chat output, e.g.:
  `>>> SIGNAL TO OTHER CC: FRANKFURT SERVER CLEANUP COMPLETE <<<`
- The operator reads that line and relays it verbatim (or paraphrased)
  into the other CC's chat.
- The receiving CC must wait for the explicit relay before proceeding
  on the gated workstream. No polling, no guessing, no racing.

### Required signal lines for this protocol
- `>>> SIGNAL TO OTHER CC: FRANKFURT SERVER CLEANUP COMPLETE <<<`
  (CC2 → CC1; unlocks CC1 Frankfurt doc moves + commit)
- `>>> SIGNAL TO OTHER CC: <topic> BLOCKED, NEED YOUR INPUT <<<`
  (either direction; used when a gated dependency is discovered mid-work)

---

## 3. Commit rules

- One workstream per commit. Never bundle Frankfurt cleanup with schema
  additions, never bundle trading config with collectibles workflows.
- Commit message format: Summary line + Description body (per operator
  feedback). Description names tables/files/workflows touched and flags
  any open items requiring operator action.
- Each CC stages and commits only files it owns under section 1. If a
  CC discovers it touched a path the other CC owns, it reverts the
  change locally and signals the other CC.
- Push only after the workstream's commit message clearly states what
  the operator must do next (Vultr destroy, n8n credential add, etc.).

---

## 4. Live-infra concurrency rules

- Never restart `n8n`, `postgres`, or `wg-quick@wg0` without explicit
  operator approval. These touch the other CC's working surface.
- Before any `docker run`, `docker compose up`, or container start on LA:
  run `free -h && docker stats --no-stream` and abort if RAM-available
  is below 200 MiB. (LA chronically runs tight — see operator memory
  on LA RAM pressure.)
- Database writes from CC must use targeted `UPDATE … WHERE id = …` or
  schema migrations under `sql/migrations/`. No ad-hoc bulk updates.
- SSH multiplexing: prefer one-shot `ssh root@<BHN_WG_LA_IP> '<cmd>'` over
  persistent sessions, so the other CC's SSH attempts are not blocked.

---

## 5. Operator-only actions (flag, don't attempt)

Both CCs must flag — never attempt — the following:
- n8n UI changes: credential add/edit, workflow import/activate, manual
  workflow refresh.
- Vultr / Hetzner control-plane actions: destroy VPS, resize, snapshot.
- Proton Pass secret retrieval. CCs generate randoms in-conversation
  and surface them in a banner once; they never read or write Proton.
- Twilio / ElevenLabs / Alpaca account-level config (keys, plans,
  phone numbers).
- Anything requiring 2FA or interactive auth.

---

## 6. End-of-session expectations

- Each CC produces a final status block listing: workstreams completed,
  workstreams partially done (with reason), commits pushed, files left
  uncommitted (with reason), and operator-action flags outstanding.
- The operator concatenates these into the day's session handoff doc
  under `infrastructure/docs/BHN session updates/BHN-SESSION-HANDOFF/`.
