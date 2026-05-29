# BHN Infrastructure Inventory

**Snapshot:** May 11, 2026. Migrated 2026-05-22 from the legacy `EVENT HORIZON VPN/BHN-INFRASTRUCTURE.txt` reference doc. Content preserved verbatim inside the code block below; **some items will be stale**. Known stale-as-of 2026-05-28:

- **Frankfurt is decommissioned** (server destroyed 2026-05-28). All FRA references in the body — node, WG keys, costs, security posture, phase progress — are historical only. Current egress map: `bhn-network-data-flow.md`. Archived design: `infrastructure/archive/frankfurt/`.
- **Grafana moved to NJ** (`http://10.8.0.5:3000`); LA package purged 2026-05-28.
- Hillsboro node was set up after the snapshot date — see `bhn-network-data-flow.md` for the current egress map.
- The phone-number parameterization plan in `bhn-horizon-phone-parameterization.md` supersedes the literal `+1 310 929 6201` reference here.

Refresh periodically as live state evolves.

---

```
================================================================================
BLACKHOLE NETWORK (BHN) — INFRASTRUCTURE REFERENCE
Last Updated: May 11, 2026
================================================================================
NOTE: Project renamed 2026-05-11 from EventHorizon VPN → Blackhole Network (BHN).
Many references below (LA paths, n8n credential names, Proton Pass entry keys,
legacy hostnames EH|VPS-LOSANGELES-US1 / EH|VPS-FRANKFURT-EU1) intentionally
keep their EH-* form per operator immutable list. The name "EventHorizon VPN"
is reserved for the future separate commercial product.
================================================================================

SERVERS
-------
LA Hub (Brain/Primary):
  Public IP:    149.28.91.100
  Tunnel IP:    10.8.0.1
  SSH:          ssh root@149.28.91.100 (password in Proton Pass: LA-VPS-Root)
  SSH via VPN:  ssh root@10.8.0.1 (key auth only)
  SSH Key:      C:\Users\fletc\.ssh\id_ed25519
  Role:         Hub, PostgreSQL, n8n/HORIZON, Grafana, WireGuard hub

Frankfurt Exit + Privacy Node:
  Public IP:    192.248.187.208
  Tunnel IP:    10.9.0.2 (wg1)
  SSH (from LA): ssh frankfurt   (alias → root@10.9.0.2:2222 via wg1 tunnel)
  SSH (direct):  Vultr blocks cross-region public TCP — direct SSH from LA does NOT work; use tunnel
  Role:         Exit node + privacy node (Tor relay + SearXNG planned per Phase 4)
                Operator "full" WG profile routes via Frankfurt's IP (Phase 1 deploy pending —
                see infrastructure/docs/bhn-frankfurt-exit-routing.md + scripts/bhn-frankfurt-exit.sh)

NJ Trading Node:
  Public IP:    140.82.4.35
  Tunnel IP:    10.8.0.5 (peer on LA's wg0 hub — not on a separate wg2)
  SSH (from LA): ssh nj         (alias → root@10.8.0.5:2222 via wg0 tunnel)
  SSH (from PC): ssh -p 2222 root@140.82.4.35   (direct works from operator PC; cross-region block only between Vultr VPSes)
  Role:         Trading node (Buffett-style screening, Congress.gov polling, Polymarket/Kalshi, Alpaca paper)
  Tunnel operational since 2026-05-12; required LA-side UFW egress allows for both 140.82.4.35:51820/udp underlay AND 10.8.0.5 inner-tunnel

WireGuard:
  Server pubkey: TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo=
  Port:          51820 (wg0 hub), 51821 (wg1 FRA)
  Operator PC:   10.8.0.4
  Phone:         10.8.0.2
  Frankfurt:     10.9.0.2 (wg1)
  NJ:            10.8.0.5 (wg0 peer)

CONSOLE TERMINOLOGY (STRICT):
  REMOTE BROWSER WINDOW = noVNC Vultr web console
  PC LA CONSOLE         = SSH session from PC to LA
  PC GE CONSOLE         = SSH session from PC to Frankfurt (via tunnel from LA)
  PC NJ CONSOLE         = SSH session from PC to NJ (direct public IP)

================================================================================
STORAGE (LA)
================================================================================

NVMe 101GB: /dev/vdb → LUKS2 → /dev/mapper/eh-nvme → XFS → /mnt/eh-nvme-hot (HOT)
HDD 399GB:  /dev/vdc → LUKS2 → /dev/mapper/eh-hdd  → XFS → /mnt/eh-hdd-cold (COLD)
Auto-unlock keyfiles: /root/.luks-eh-nvme, /root/.luks-eh-hdd
Backup passphrases in Proton Pass: EH-NVMe-LUKS, EH-HDD-LUKS

================================================================================
N8N — DOCKER (upgraded May 10, 2026)
================================================================================

Version: 2.19.5 (Docker container, NOT systemd)
Access:  http://10.8.0.1:5678 (VPN only)

Docker run command:
  ENC_KEY=$(jq -r '.encryptionKey' /root/.n8n/config)
  sudo docker run -d --name n8n --restart unless-stopped \
    --network=host \
    -v /root/.n8n:/home/node/.n8n \
    -e N8N_ENCRYPTION_KEY="$ENC_KEY" \
    -e N8N_HOST=10.8.0.1 \
    -e N8N_PORT=5678 \
    -e N8N_PROTOCOL=http \
    -e N8N_SECURE_COOKIE=false \
    n8nio/n8n:latest

Docker commands:
  sudo docker ps              # check status
  sudo docker logs n8n        # view logs
  sudo docker restart n8n     # restart
  sudo docker stop n8n        # stop

CRITICAL RULES (learned the hard way):
  NEVER refresh browser with n8n editor open
  NEVER use readfile() in SQLite UPDATE on workflow_entity
  NEVER use n8n CLI import:workflow on 2.8.4
  ALWAYS test chat via external webhook URL in separate tab
  ALWAYS snapshot DB after every save:
    cp /root/.n8n/database.sqlite /root/.n8n/database.sqlite.snap-$(date +%s)

n8n API Key:        Proton Pass → EH-N8N-API-Key
n8n Encryption Key: Proton Pass → EH-N8N-EncryptionKey

================================================================================
HORIZON (AI AGENT)
================================================================================

Status: OPERATIONAL (recovered May 10, 2026)

Workflow ID:  fTFjaf2Q2aQrOPsY
WebhookId:    ec1592c6-8715-4b0f-8ee8-5bc02f551a27
Chat URL:     http://10.8.0.1:5678/webhook/ec1592c6-8715-4b0f-8ee8-5bc02f551a27/chat

Identity:
  Name:    HORIZON (named after Event Horizon Telescope)
  Voice:   Charlotte (ElevenLabs, British female, Creator plan $22/mo)
  Email:   horizon@eventhorizonvpn.com (Proton Mail)
  Phone:   +1 310 929 6201 (Twilio)

Known issues:
  - Token logging disconnected (needs rewire as parallel branch)
  - public: false on chatTrigger (works in browser, curl 404s)
  - Format Memory Block disconnected (Code node bug in 2.8.4)

Publish command if needed:
  sudo docker exec n8n n8n publish:workflow --id=fTFjaf2Q2aQrOPsY

================================================================================
POSTGRESQL
================================================================================

Database: eventhorizon
Host:     10.8.0.1:5432 (VPN tunnel only)

Roles:
  ehuser          → collector scripts
  n8n_user        → n8n workflows
  agent_reader    → HORIZON read-only
  grafana_reader  → Grafana
  postgres        → superuser
  bootstrap_writer→ new node registration

Tables:
  sessions, security_events, anomalies, pulse_reports,
  agent_token_log, nodes, memories (pgvector 384-dim),
  call_transcripts, market_signals, ebay_watchlist,
  trading_rules, qa_cache, node_logs

================================================================================
CREDENTIALS IN N8N (all intact)
================================================================================

  EH-Twilio                         (Twilio API)
  EH-ElevenLabs                     (Header Auth: xi-api-key)
  EH-Horizon-Email                  (SMTP: horizon@eventhorizonvpn.com)
  EH-NewsAPI                        (Header Auth: X-Api-Key)
  EH-OpenWeatherMap                 (OpenWeatherMap API)
  Postgres EventHorizon             (read-write)
  Postgres EventHorizon (agent read-only)
  EventHorizonVPN-Claude            (Anthropic API)

================================================================================
PROTON PASS — KEY ENTRIES
================================================================================

  LA-VPS-Root                    LA root password
  EH-FRA-Root-2026-05-08         Frankfurt root password
  EH-NVMe-LUKS                   NVMe encryption passphrase
  EH-HDD-LUKS                    HDD encryption passphrase
  EH-Grafana-Admin               Grafana admin
  EH-n8n-Admin-2026-05-08        n8n admin
  EH-Postgres-ehuser-2026-05-08  PostgreSQL ehuser
  EH-N8N-API-Key                 n8n REST API key
  EH-N8N-EncryptionKey           n8n credential encryption key
  EH-Twilio-AccountSID           Twilio account SID
  EH-Twilio-AuthToken            Twilio auth token
  EH-Twilio-PhoneNumber          +1 310 929 6201
  EH-ElevenLabs-APIKey           ElevenLabs API key
  EH-ElevenLabs-HorizonVoiceID   Charlotte voice ID
  EH-OpenWeatherMap-APIKey       Weather API
  EH-NewsAPI-APIKey              News API
  EH-HORIZON-Chat-URL            HORIZON webhook chat URL
  EH-Horizon-SMTP-Token          Proton SMTP token

================================================================================
SECURITY
================================================================================

LA US1:
  WireGuard + PSK, CrowdSec, Suricata, Fail2ban
  UFW default DROP, dnscrypt-proxy, Shadowsocks
  LUKS2, SSH key-only

Frankfurt:
  WireGuard + PSK, CrowdSec, Suricata
  UFW cleaned, SSH key-only

CVE-2026-31431 "Copy Fail":
  Status: MITIGATED (algif_aead blacklisted both nodes)
  Full fix needed: apt update && apt upgrade -y && reboot (LA + Frankfurt)

================================================================================
GITHUB
================================================================================

Repo: FletchEm31/BLACKHOLE-NETWORK (main branch — renamed from EVENT-HORIZON-VPN-DASHBOARD on 2026-05-11)

Structure:
  infrastructure/bootstrap/    v4 modular bootstrap (21 files)
  infrastructure/docs/         HORIZON roadmap, voice stack, data architecture
  scripts/                     production scripts
  n8n-workflows/               bhn-horizon.json, bhn-pulse-2h.json, bhn-proxy-health-monitor.json
  sql/                         schemas
  infrastructure/grafana/      fleet health dashboard

Bootstrap command:
  bash infrastructure/bootstrap/bhn-node-bootstrap.sh NAME IP WG_INTERFACE TYPE REGION
  Types: hub, exit, scan, proxy

================================================================================
NAMING CONVENTION
================================================================================

Standalone VPS (new nodes): BHN|VPS-LOCATION-COUNTRY+SEQINDEX
  Example: BHN|VPS-NEWJERSEY-US2 (trading node, provisioning 2026-05-11)

Legacy nodes (operator renames manually):
  EH|VPS-LOSANGELES-US1   (LA hub)
  EH|VPS-FRANKFURT-EU1    (Frankfurt exit)

Attached storage: SSD-LOSANGELES-US1 (no VPS prefix)
Format:           DEVICE-LOCATION-COUNTRY+SEQINDEX

================================================================================
COSTS (~$185-200/month)
================================================================================

  LA VPS:        $12
  Frankfurt VPS: $12
  NVMe:          $10.10
  HDD:           $9.97
  Backups:       $2.40
  ElevenLabs:    $22 (Creator)
  Twilio:        ~$16 (number $1.15 + usage)
  Claude Max:    $100-200
  API costs:     $3-5

  (Personal infrastructure — no users, no revenue model. Future commercial
  EventHorizon VPN product is a separate concern on separate infra.)

================================================================================
PHASE PROGRESS
================================================================================

Phase 1 NETWORK:   ~85% (Frankfurt traffic routing still needed)
Phase 2 DASHBOARD: ~65%
Phase 3 AI:        ~45% (HORIZON operational, voice/SMS not built)

================================================================================
```
