# BHN Netdata fleet

Per-node Netdata agents installed 2026-05-28, with LA (`10.8.0.1`) acting as
the streaming parent. NJ, Hillsboro, and Frankfurt stream their metrics to
LA so a single dashboard at `http://10.8.0.1:19999` shows the whole fleet.

## URLs

| Node | Per-node UI | Role |
|------|-------------|------|
| LA | http://10.8.0.1:19999 | Parent — aggregates all four |
| NJ | http://10.8.0.5:19999 | Child |
| Hillsboro | http://10.8.0.6:19999 | Child |
| Frankfurt | http://10.9.0.2:19999 | Child |

All four are UFW-restricted to `10.8.0.0/24` + `10.9.0.0/24`.

## Files

- `stream.conf.parent` — template for LA's `/opt/netdata/etc/netdata/stream.conf` (static-kickstart install path).
- `stream.conf.child` — template for the three children. Path depends on install type (see file header).

The shared API key is **not** committed — it's a UUID held by the operator;
substitute for `<APIKEY>` at deploy time. Same UUID on parent and all children.

## Install types per node

| Node | Install method | Config dir |
|------|----------------|------------|
| LA | static-kickstart binary (`--install-type any`) | `/opt/netdata/etc/netdata/` |
| Hillsboro | static-kickstart binary (`--install-type any`) | `/opt/netdata/etc/netdata/` |
| NJ | Ubuntu apt package | `/etc/netdata/` |
| Frankfurt | Ubuntu apt package | `/etc/netdata/` |

LA and Hillsboro fell back to the static binary because the kickstart's
native-package detection failed on Ubuntu 22.04 / EPYC. Functional
equivalence; auto-updates land via `/etc/cron.daily/netdata-updater`.

## Gotcha that bit us

On Netdata v2.x the parent's `[API_KEY]` section **requires `type = api`**.
The upstream stream.conf template states "YOU MUST SET THIS FIELD ON ALL API
KEYS" but doesn't fail loud if it's missing — children silently can't
authenticate, and no error appears in either side's log. Symptom: parent
shows only itself in `/api/v2/nodes`, children show their own hostname in
`mirrored_hosts` (no parent visible). Fix: add `type = api` as the first
setting under the `[<UUID>]` section header on the parent.
