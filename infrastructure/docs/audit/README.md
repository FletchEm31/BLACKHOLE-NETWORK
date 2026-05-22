# BHN Comprehensive Audit — Workspace

Working area for the comprehensive BHN audit (started 2026-05-22).

## Layout

| Path | Purpose |
|------|---------|
| `audit-plan.md` | The formal audit plan (added when ready) |
| `screenshots/` | Operator drop folder — screenshots, diagrams, dashboards, scans for Claude Code to review |
| `findings-code.md` | Claude Code's code + live-state audit findings (output) |
| `findings-*.md` | Other workstream findings as they land |

## Roles (per `../collaboration-model.md`)

- **App Claude** — docs / architecture review, n8n. Reads this folder via GitHub.
- **Claude Code** — code audit + live-state verification (repo-vs-deployed, DB schema vs `eventhorizon`, FK/constraint checks, git history). Sole writer of committed findings.

## How to use the screenshots drop

Operator saves images into `screenshots/` and tells Claude Code the filename; Claude Code views them via its Read tool (PNG/JPG/PDF supported).

> **Screenshots are local-only.** `screenshots/.gitignore` excludes everything in that folder from git (images can carry IPs/tokens/PII). They stay on the operator's machine; Claude Code reads them locally for the audit, but they never enter git history or App Claude's read window. Findings derived from them go into the `findings-*.md` docs (text), which are committed.
