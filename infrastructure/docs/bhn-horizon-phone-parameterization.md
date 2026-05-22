# HORIZON Phone-Number Parameterization — Plan

Remove the two hardcoded phone numbers from `n8n-workflows/bhn-horizon.json` and replace them with environment-variable references, so PII isn't committed to the repo.

**Status:** plan only. **Not yet executed** — and deliberately so: this requires a **live n8n edit first**, then re-export. Editing the committed JSON alone would diverge it from the running flow.

---

## What's hardcoded today

Both numbers live in one node — **"Send an SMS/MMS/WhatsApp message in Twilio"** (`n8n-nodes-base.twilioTool`):

| Field | Value | Meaning |
|-------|-------|---------|
| `from` | `+13109296201` | HORIZON's Twilio sending number |
| `to` | `+13149732859` | Operator's personal cell (SMS destination) |

(The committed `bhn-horizon.json` is an export of an `active: true` flow.)

## Why this is NOT a repo-only change

n8n credential *data* is already stripped on export (credentials are id+name references). But these phone numbers are plain node parameters, so they export literally. The fix has to happen **in n8n**, because:

1. The repo JSON mirrors the live flow. If we hand-edit the JSON to use `{{ $env.X }}` but the live flow still has literals, the repo and the running flow diverge.
2. If someone later re-imports the repo JSON to "restore" the flow **without** the env vars set on the host, SMS silently breaks (sends from/to empty) — a worse failure than committed PII in a private repo.

So the order is: **set env vars on the n8n host → edit the live flow to reference them → re-export → commit the clean JSON.** Repo-side, Claude Code only commits the final re-exported file.

## Proposed env-var names

Following the live-host `EH_*` env-var convention (a preserved deployed-state contract — see `collaboration-model.md`). Names are a proposal; operator picks final:

| Env var | Holds |
|---------|-------|
| `EH_HORIZON_TWILIO_FROM` | `+13109296201` (Twilio sending number) |
| `EH_HORIZON_OPERATOR_PHONE` | `+13149732859` (operator cell) |

In the Twilio node, the fields become n8n expressions:
- `from` → `={{ $env.EH_HORIZON_TWILIO_FROM }}`
- `to` → `={{ $env.EH_HORIZON_OPERATOR_PHONE }}`

## Execution steps (live session, operator + Claude Code)

1. **Set env vars on the n8n host** (LA). n8n reads process env at start, so add to the systemd unit env (consistent with how `la-egress-lockdown` injects n8n proxy vars via `systemd/n8n.service.d/`):
   ```ini
   # /etc/systemd/system/n8n.service.d/horizon-env.conf
   [Service]
   Environment=EH_HORIZON_TWILIO_FROM=+13109296201
   Environment=EH_HORIZON_OPERATOR_PHONE=+13149732859
   ```
   Then `systemctl daemon-reload && systemctl restart n8n`.
   > Note: this moves the numbers out of the repo, but they now live in a file on LA. That's fine — the goal is "not in version control / not in App Claude's read window," not "secret." (These aren't secrets; they're PII.)
2. **Verify n8n sees them:** `systemctl show n8n -p Environment | tr ' ' '\n' | grep EH_HORIZON`.
3. **Edit the live HORIZON flow** in the n8n UI: set the Twilio node's `from`/`to` to the two expressions above. Save. Send a test SMS to confirm both resolve.
4. **Re-export** the flow: `n8n export:workflow --id <HORIZON-id> --output n8n-workflows/bhn-horizon.json` (overwrite the repo copy).
5. **Claude Code commits** the re-exported JSON — confirm via `git diff` that the only change is the two fields becoming `$env` expressions (no literals remain, no unrelated node churn). Summary + Description on the commit.
6. **Scrub history (optional):** the numbers remain in prior commits. Private repo, low urgency; if desired, a later `git filter-repo` pass — separate decision, not part of this change.

## Acceptance check

```bash
grep -E "\+1310929|\+1314973" n8n-workflows/bhn-horizon.json   # → no matches after step 4
grep -o "EH_HORIZON_[A-Z_]*" n8n-workflows/bhn-horizon.json    # → both env refs present
```

## Related

- `collaboration-model.md` — why repo-committed PII matters (App Claude read window) + `EH_*` preservation contract
- `infrastructure/la-egress-lockdown/systemd/n8n.service.d/` — precedent for injecting n8n env via systemd drop-in
