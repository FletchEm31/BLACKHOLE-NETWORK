# PostgreSQL pg_hba.conf — Access Control Notes

Production file: `/etc/postgresql/14/main/pg_hba.conf` on LA hub (10.8.0.1).
Reload after changes: `systemctl reload postgresql`

## Lines added this session (2026-06-25)

```
host  eventhorizon  ehuser  10.8.0.1/32  scram-sha-256
```

Allows the `ehuser` role to connect to `eventhorizon` from the WireGuard hub IP only.
Added to resolve peer auth failure for hub-local service connections.

## Standard entries (reference)

```
# Trading node (NJ) — bhn_trader reads/writes trading_* tables
host  eventhorizon  bhn_trader  10.8.0.5/32  scram-sha-256

# Grafana reader — read-only across all tables (Grafana on LA hub)
host  eventhorizon  grafana_reader  10.8.0.1/32  scram-sha-256

# HORIZON agent reader — read-only for AI agent
host  eventhorizon  agent_reader  10.8.0.1/32  scram-sha-256

# n8n on LA hub — workflow automation access
host  eventhorizon  n8n_user  10.8.0.1/32  scram-sha-256
```

## Roles reference

| Role | Privileges | Used by |
|---|---|---|
| `postgres` | Superuser | Admin only (unix socket, peer auth) |
| `bhn_trader` | INSERT/UPDATE on trading_* | NJ Python trading scripts |
| `grafana_reader` | SELECT all tables | Grafana on LA |
| `agent_reader` | SELECT all tables | HORIZON AI agent |
| `ehuser` | Application role | Hub-local services |
| `n8n_user` | SELECT + INSERT on specific tables | n8n workflows |

## Passwords

All passwords stored in Proton Pass under the `BHN-PostgreSQL-*` naming convention.
