# Dozzle — BHN Unified Docker Log Viewer

**UI:** `http://<BHN_WG_LA_IP>:9999` (WireGuard tunnel only)

Dozzle runs as a Docker container on LA and connects to lightweight agents on NJ and Hillsboro. All three nodes' Docker logs are visible from a single UI.

## Architecture

```
Operator PC (<BHN_WG_OPC_IP>)
  │  browser → http://<BHN_WG_LA_IP>:9999
  ▼
LA hub  bhn-dozzle (port 9999)
  │  connects via WG tunnel to agents on :7007
  ├──► NJ  bhn-dozzle-agent (<BHN_WG_NJ_IP>:7007)
  └──► Hillsboro  bhn-dozzle-agent (<BHN_WG_HIL_IP>:7007)
```

## Deploy hub (LA)

```bash
mkdir -p /opt/bhn-dozzle
# copy docker-compose-hub.yml → /opt/bhn-dozzle/docker-compose.yml
docker compose up -d
```

Add UFW rule to keep port 9999 mesh-only (if not already restricted by default DROP):
```bash
ufw allow in on wg0 to <BHN_WG_LA_IP> port 9999 proto tcp comment "dozzle mesh-only"
```

## Deploy agents (NJ + Hillsboro)

### NJ — install Docker first (not present by default)
```bash
ssh -p 2222 root@<BHN_WG_NJ_IP> '
  apt-get update && apt-get install -y docker.io
  systemctl enable --now docker
'
```

### Both NJ and Hillsboro
```bash
for node_ssh in "root@<BHN_WG_NJ_IP> -p 2222" "root@<BHN_WG_HIL_IP>"; do
  ssh $node_ssh 'mkdir -p /opt/bhn-dozzle'
done

# Copy agent compose file
scp -P 2222 infrastructure/services/dozzle/docker-compose-agent.yml \
    root@<BHN_WG_NJ_IP>:/opt/bhn-dozzle/docker-compose.yml
scp infrastructure/services/dozzle/docker-compose-agent.yml \
    root@<BHN_WG_HIL_IP>:/opt/bhn-dozzle/docker-compose.yml

# Start agents
ssh -p 2222 root@<BHN_WG_NJ_IP> 'cd /opt/bhn-dozzle && docker compose up -d'
ssh root@<BHN_WG_HIL_IP> 'cd /opt/bhn-dozzle && docker compose up -d'
```

## Verify

```bash
# Hub sees agents
docker logs bhn-dozzle | grep -i agent

# Hit the UI
curl -sf http://<BHN_WG_LA_IP>:9999/ | head -5
```

## Ports

| Port | Node | Binding | Purpose |
|------|------|---------|---------|
| 9999 | LA | <BHN_WG_LA_IP>:9999 | Dozzle UI (mesh-only) |
| 7007 | NJ | 0.0.0.0:7007 (WG-internal) | Agent listener |
| 7007 | Hillsboro | 0.0.0.0:7007 (WG-internal) | Agent listener |
