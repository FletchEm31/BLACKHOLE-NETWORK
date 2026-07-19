# PostgreSQL pg_hba.conf — Access Control Notes

Production file: `/etc/postgresql/14/main/pg_hba.conf` on LA hub (<BHN_WG_LA_IP>).
Reload after changes: `systemctl reload postgresql`

## Lines added 2026-07-19

```
host    eventhorizon    plotly_studio_reader    10.8.0.0/24    scram-sha-256
```

Read-only role for the upcoming Plotly Studio-generated Dash analytics app
(see sql/migrations/2026-07-19-plotly-studio-reader-role.sql — SELECT-only
on 33 weather_* bronze/silver/gold/position tables, no write access).
Scoped to the full mesh subnet, not a single `/32`, because the connecting
client (wherever Plotly Studio actually runs) isn't a fixed node yet — it
must be a WireGuard peer on 10.8.0.0/24, same as every other role in this
file. listen_addresses on LA already included `10.8.0.1` before this
change (used by bhn_weather_collector from Hillsboro/Helsinki); the gap
was purely pg_hba — every role here needs its own explicit line, there is
no wildcard "any role from mesh" rule. Verified end-to-end with a live TCP
connection (not just `pg_hba_file_rules` parsing): SELECT succeeds, INSERT
correctly returns `permission denied for table`.

## Lines added this session (2026-06-25)

```
host  eventhorizon  ehuser  <BHN_WG_LA_IP>/32  scram-sha-256
```

Allows the `ehuser` role to connect to `eventhorizon` from the WireGuard hub IP only.
Added to resolve peer auth failure for hub-local service connections.

## Standard entries (reference)

```
# Trading node (NJ) — bhn_trader reads/writes trading_* tables
host  eventhorizon  bhn_trader  <BHN_WG_NJ_IP>/32  scram-sha-256

# Grafana reader — read-only across all tables (Grafana on LA hub)
host  eventhorizon  grafana_reader  <BHN_WG_LA_IP>/32  scram-sha-256

# HORIZON agent reader — read-only for AI agent
host  eventhorizon  agent_reader  <BHN_WG_LA_IP>/32  scram-sha-256

# n8n on LA hub — workflow automation access
host  eventhorizon  n8n_user  <BHN_WG_LA_IP>/32  scram-sha-256
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
| `plotly_studio_reader` | SELECT-only on weather_* analytics surface (33 tables) | Plotly Studio Dash app |

## Passwords

All passwords stored in Proton Pass under the `BHN-PostgreSQL-*` naming convention.
