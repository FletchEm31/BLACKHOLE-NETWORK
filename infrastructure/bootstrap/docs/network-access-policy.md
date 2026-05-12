# Blackhole Network (BHN) — Network Access Policy

The bootstrap toolchain treats firewall posture as **declarative data**, not procedural code. Each node type has a policy file under `policies/` that lists exactly which ports the node accepts inbound, what egress it's allowed to make, and which interfaces it forwards traffic between.

The policy file is the source of truth. UFW state is downstream output.

## Why declarative

Procedural firewall scripts drift. Someone runs `ufw allow 8443` for a debug session and forgets to delete it. Six months later, no one knows whether 8443 is intentional. CI for firewall rules is hard because the "correct state" lives only in tribal memory.

A policy file fixes both problems:

- Diffs against `git log` answer "when was this opened, by whom, why"
- An audit script can read the live UFW state and the policy file, and flag any divergence
- New nodes of the same type are guaranteed to start with the same posture

## File format

One rule per line. Lines starting with `#` and blank lines are ignored. Inline comments after `#` are stripped at parse time but **strongly encouraged for human readers** — every rule should answer "why is this open?"

### Grammar

```
OUTBOUND_DEFAULT <allow|deny>
INBOUND  <port>/<proto>|any [from <cidr>] [on <iface>]   # comment
OUTBOUND <port>/<proto>|any [to <cidr>]                  # comment
FORWARD  any in <iface> out <iface>                      # comment
```

### Tokens

| Token             | Meaning                                                                |
|-------------------|------------------------------------------------------------------------|
| `<port>/<proto>`  | e.g. `22/tcp`, `53/udp`. `<proto>` is `tcp` or `udp`.                  |
| `any`             | Wildcard port + proto — only valid when paired with `from`/`to <cidr>`. |
| `from <cidr>`     | Restrict inbound to a source CIDR (e.g. `10.8.0.0/24`).                |
| `to <cidr>`       | Restrict outbound to a destination CIDR or single IP.                  |
| `on <iface>`      | Restrict to traffic arriving on a specific interface.                  |

### Placeholders

These are substituted at apply time by the bootstrap, so the same policy file works on any node:

| Placeholder              | Substituted with                              |
|--------------------------|-----------------------------------------------|
| `<HUB_IP>`               | `$HUB_IP` (default `149.28.91.100`)           |
| `<HUB_TUNNEL_NETWORK>`   | `10.8.0.0/24` (hub's wg0 subnet)              |
| `<WG_INTERFACE>`         | `$WG_INTERFACE` (e.g. `wg0`, `wg2`)           |
| `<NET_IFACE>`            | Default route's NIC (e.g. `enp1s0`, `eth0`)   |

### `OUTBOUND_DEFAULT`

The default egress posture. `deny` enables strict whitelisting (every outbound flow needs an explicit `OUTBOUND` rule); `allow` permits anything not explicitly blocked. **All node types should default to `deny`.**

### `FORWARD`

Currently supports the only pattern in production: peer transit through a node. The bootstrap also installs a NAT MASQUERADE rule on the egress NIC for any `FORWARD` line — this is what makes hub-style egress work.

## How rules become UFW commands

The parser in `modules/network-policy.sh` translates each line:

| Policy line                                  | UFW invocation                                                         |
|----------------------------------------------|------------------------------------------------------------------------|
| `INBOUND 22/tcp`                             | `ufw allow 22/tcp`                                                     |
| `INBOUND 22/tcp from 10.8.0.0/24`            | `ufw allow from 10.8.0.0/24 to any port 22 proto tcp`                  |
| `INBOUND 51821/udp from 149.28.91.100`       | `ufw allow from 149.28.91.100 to any port 51821 proto udp`             |
| `OUTBOUND 443/tcp`                           | `ufw allow out 443/tcp`                                                |
| `OUTBOUND 51821/udp to 192.248.187.208`      | `ufw allow out to 192.248.187.208 port 51821 proto udp`                |
| `OUTBOUND any to 10.9.0.0/24`                | `ufw allow out to 10.9.0.0/24`                                         |
| `FORWARD any in wg0 out enp1s0`              | `ufw route allow in on wg0 out on enp1s0` + `iptables -t nat MASQUERADE` |
| `OUTBOUND_DEFAULT deny`                      | `ufw default deny outgoing`                                            |

## Per-node-type intent

| Type    | Public ingress         | Tunnel ingress             | Egress posture          |
|---------|------------------------|----------------------------|-------------------------|
| `hub`   | 22, 80, 443, 51820, 8388 | 3000 (Grafana), 5432 (PG), 5678 (n8n), 53 | strict whitelist; FRA tunnel allowed |
| `exit`  | 22, 51821 (from hub), 8388 (from hub) | 22 from hub tunnel | strict whitelist; mgmt plane to hub |
| `scan`  | 22, 51821 (from hub)   | 9090 (metrics), 5140 (syslog), 22 (admin from hub) | strict whitelist; PG-write to hub |
| `proxy` | 22, 8388, 51821 (from hub) | 22, 9090 (mgmt from hub) | strict whitelist; mgmt plane to hub |

## Editing a policy

1. Edit `policies/<type>-network-policy.conf` in the repo.
2. Add an inline comment explaining the change — future-you will thank you.
3. Commit with a message that says **why** (link an incident, ticket, or threat scenario).
4. To re-apply on a running node: `bash infrastructure/bootstrap/modules/network-policy.sh apply policies/<type>-network-policy.conf` (planned helper — currently re-run the bootstrap with same args).

## Drift detection (planned)

A `eh-policy-audit` cron job will read the live UFW state, parse the live policy, and emit a metric to Grafana when they diverge. Any rule in UFW not in the policy file is flagged; any rule in the policy not in UFW is flagged. Surface this in the existing Grafana "EH Network Overview" dashboard.

## Why not iptables directly?

UFW is a thin abstraction over iptables, but it gives us:
- Named comments (`# n8n-from-tunnel`) that show in `ufw status`
- Stable rule serialization that `git diff` can read
- A persistence layer that survives reboot without us writing systemd units
- Declarative defaults (`ufw default deny outgoing`) that iptables doesn't have

When UFW isn't enough — the FORWARD chain + NAT MASQUERADE — we drop to iptables explicitly in the parser and persist via `netfilter-persistent`.
