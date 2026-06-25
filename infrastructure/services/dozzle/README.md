# Dozzle — BHN Unified Docker Log Viewer

**UI:** `http://10.8.0.1:9999` (WireGuard tunnel only)

Dozzle runs as a Docker container on LA and connects to lightweight agents on NJ and Hillsboro. All three nodes' Docker logs are visible from a single UI.

## Architecture

```
Operator PC (10.8.0.4)
  │  browser → http://10.8.0.1:9999
  ▼
LA hub  bhn-dozzle (port 9999)
  │  connects via WG tunnel to agents on :7007
  ├──► NJ  bhn-dozzle-agent (10.8.0.5:7007)
  └──► Hillsboro  bhn-dozzle-agent (10.8.0.6:7007)
```

## Deploy hub (LA)

```bash
mkdir -p /opt/bhn-dozzle
# copy docker-compose-hub.yml → /opt/bhn-dozzle/docker-compose.yml
docker compose up -d
```

Add UFW rule to keep port 9999 mesh-only (if not already restricted by default DROP):
```bash
ufw allow in on wg0 to 10.8.0.1 port 9999 proto tcp comment "dozzle mesh-only"
```

## Deploy agents (NJ + Hillsboro)

### NJ — install Docker first (not present by default)
```bash
ssh -p 2222 root@10.8.0.5 '
  apt-get update && apt-get install -y docker.io
  systemctl enable --now docker
'
```

### Both NJ and Hillsboro
```bash
for node_ssh in "root@10.8.0.5 -p 2222" "root@10.8.0.6"; do
  ssh $node_ssh 'mkdir -p /opt/bhn-dozzle'
done

# Copy agent compose file
scp -P 2222 infrastructure/services/dozzle/docker-compose-agent.yml \
    root@10.8.0.5:/opt/bhn-dozzle/docker-compose.yml
scp infrastructure/services/dozzle/docker-compose-agent.yml \
    root@10.8.0.6:/opt/bhn-dozzle/docker-compose.yml

# Start agents
ssh -p 2222 root@10.8.0.5 'cd /opt/bhn-dozzle && docker compose up -d'
ssh root@10.8.0.6 'cd /opt/bhn-dozzle && docker compose up -d'
```

## Verify

```bash
# Hub sees agents
docker logs bhn-dozzle | grep -i agent

# Hit the UI
curl -sf http://10.8.0.1:9999/ | head -5
```

## Ports

| Port | Node | Binding | Purpose |
|------|------|---------|---------|
| 9999 | LA | 10.8.0.1:9999 | Dozzle UI (mesh-only) |
| 7007 | NJ | 0.0.0.0:7007 (WG-internal) | Agent listener |
| 7007 | Hillsboro | 0.0.0.0:7007 (WG-internal) | Agent listener |
