# DNS qname logging — disabled 2026-05-23

Privacy-debt finding from the 2026-05-23 LA infra audit, resolved same session.

## What was on

| Surface | State found |
|---|---|
| `dnscrypt-proxy` `[query_log]` (disk) | **ON** — `/var/log/dnscrypt-proxy/query.log` was 8.1 MiB, growing; one rotated `.gz` from 2026-05-20 also present. Captured `qname + client_ip + qtype + resolver` per resolution. |
| `dns_query_log` PG table | **8,788 rows** at TRUNCATE time (49,999 at audit time — `eh-purge` had thinned it in between). Same content as disk: `qname, client_ip, qtype, status, resolver`. **No** answer payloads. |
| `bhn-dns-log-collector` cron | Scheduled every 5 min but silently broken — `/var/log/bhn-dns-log-collector.log` did not exist and table ingestion had stopped 2026-05-13. |
| Suricata `pcap-log` | Already `enabled: false`. Zero pcap files. (Not changed — already correct.) |

## What was done

1. **Backed up** `/etc/dnscrypt-proxy/dnscrypt-proxy.toml` → `dnscrypt-proxy.toml.bak-20260523T181209Z` on LA.
2. **Commented out** the `file =` line inside the `[query_log]` block:
   `# DISABLED 2026-05-23 privacy:   file = '/var/log/dnscrypt-proxy/query.log'`
3. **Restarted** `dnscrypt-proxy.service` — verified `active (running)`, verified resolution still works (`dig @127.0.0.1 example.com` returned expected A records).
4. **Wiped** disk-side query logs:
   - `/var/log/dnscrypt-proxy/query.log` (8.1 MiB)
   - `/var/log/dnscrypt-proxy/query-2026-05-20T15-32-27.878.log.gz` (905 KiB)
5. **TRUNCATEd** `dns_query_log` table with `RESTART IDENTITY` — 8,788 rows → 0.
6. **Removed** `/etc/cron.d/bhn-dns-log-collector` — moved to `/root/bhn-backups/bhn-dns-log-collector.removed-20260523T181209Z` for rollback if ever needed.

## What is still on (out of scope this round)

- `/var/log/dnscrypt-proxy/nx.log` (158 KiB) — NXDOMAIN qnames, same privacy character as `query.log`. Was not in the approved cleanup scope this session; operator should decide whether to also disable `[nx_log]` in the toml.
- `dnscrypt-proxy` itself still uses upstream resolvers (Cloudflare, NextDNS visible in old log samples). That's a separate policy decision about who sees DNS upstream; not a local-storage issue.

## Why it matters

The disk log + PG table together paired **client tunnel IPs** (e.g. `<BHN_WG_OPC_IP>`) with **resolved qnames** (e.g. `v20.events.data.microsoft.com`). For a solo-operator network today the only identifiable client is the operator, but per BHN's external-observer principle no qname↔client_ip pairing should be retained at rest. Disabling at the source (dnscrypt-proxy) is the durable fix; truncating the table and wiping the disk logs clears the historical debt.

## Reversal procedure (if ever needed)

1. Edit `/etc/dnscrypt-proxy/dnscrypt-proxy.toml`, uncomment the `file =` line.
2. `systemctl restart dnscrypt-proxy`.
3. Restore the cron: `mv /root/bhn-backups/bhn-dns-log-collector.removed-* /etc/cron.d/bhn-dns-log-collector` (note: ingester script was broken at removal time — fix it before re-enabling, or it'll silently no-op again).

The `dns_query_log` table schema is preserved (we used TRUNCATE, not DROP) so historical re-enabling would not require a schema migration.
