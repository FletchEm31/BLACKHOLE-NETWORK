# BHN Wireshark Capture Workflow

Packet captures are taken on individual BHN nodes using `tshark`, pulled to the operator PC for live analysis, then archived to the BHN-BLACKBOX Cryptomator vault.

---

## Storage Map

### Node-local (on each server)
Captures are saved under `/root/WireShark/[NODE-FOLDER]/` on the node:

| Subfolder | Contents |
|-----------|----------|
| `captures/` | Raw `.pcapng` files from `tshark` |
| `exports/` | Filtered exports, specific flows |
| `notes/` | `.txt` analysis notes |

### Operator PC (staging)
`C:\Users\fletc\Cryptometer\` — temporary landing zone for SCP pulls. Open files here in Wireshark for live analysis.

### BHN-BLACKBOX (archive)
`E:\WireShark\[NODE-FOLDER]\` — encrypted Cryptomator vault. Same `captures\` / `exports\` / `notes\` subfolder structure per node.

#### Node folder names
| Node | BLACKBOX folder |
|------|----------------|
| LA Hub | `BHN-US1-LOSANGELES-HUB\` |
| NJ | `BHN-US2-NEWJERSEY-HF\` |
| Hillsboro | `BHN-US3-HILLSBORO-EXIT\` |
| Helsinki | `BHN-EU1-HELSINKI-EXIT\` |
| Operator desktop | `FLETCH-DESKTOP\` |

---

## Step 1 — Capture on the Node

SSH to the node and run `tshark`. Common invocations:

```bash
# Capture all traffic on primary interface, save to node-local exports folder:
IFACE=$(ip route | awk '/^default/{print $5; exit}')
OUTFILE=/root/WireShark/BHN-US3-HILLSBORO-EXIT/exports/hillsboro-$(date +%Y%m%d-%H%M%S).pcapng
tshark -i "$IFACE" -w "$OUTFILE"
# Ctrl+C to stop

# WireGuard interface only:
tshark -i wg0 -w /root/WireShark/BHN-US1-LOSANGELES-HUB/exports/la-wg0-$(date +%Y%m%d-%H%M%S).pcapng

# Capture with ring buffer (100MB × 5 files, auto-rotate):
tshark -i "$IFACE" -b filesize:102400 -b files:5 -w "$OUTFILE"
```

Install tshark if missing: `apt-get install -y tshark`

---

## Step 2 — SCP Pull to Operator PC (Staging)

Run from PowerShell on the operator PC. Files land in `C:\Users\fletc\Cryptometer\` for immediate Wireshark analysis.

```powershell
# Hillsboro (WireGuard tunnel, port 22):
scp root@10.8.0.6:/root/WireShark/BHN-US3-HILLSBORO-EXIT/exports/<file>.pcapng "C:\Users\fletc\Cryptometer\"

# NJ (public IP, port 2222):
scp -P 2222 root@<BHN_NJ_PUBLIC_IP>:/root/WireShark/BHN-US2-NEWJERSEY-HF/exports/<file>.pcapng "C:\Users\fletc\Cryptometer\"

# LA (WireGuard tunnel, port 22):
scp root@10.8.0.1:/root/WireShark/BHN-US1-LOSANGELES-HUB/exports/<file>.pcapng "C:\Users\fletc\Cryptometer\"

# Helsinki (WireGuard tunnel, port 22 — once bootstrapped):
scp root@10.8.0.8:/root/WireShark/BHN-EU1-HELSINKI-EXIT/exports/<file>.pcapng "C:\Users\fletc\Cryptometer\"
```

Pull all exports from a node at once:
```powershell
scp root@10.8.0.6:/root/WireShark/BHN-US3-HILLSBORO-EXIT/exports/*.pcapng "C:\Users\fletc\Cryptometer\"
```

Check what's in staging:
```powershell
Get-ChildItem "C:\Users\fletc\Cryptometer\"
```

---

## Step 3 — Archive to BHN-BLACKBOX

After analysis, move files from staging to the vault. BHN-BLACKBOX must be online and Cryptomator vault unlocked (mounted as `E:`).

```powershell
# Move to appropriate node folder + subfolder:
Move-Item "C:\Users\fletc\Cryptometer\<file>.pcapng" "E:\WireShark\BHN-US3-HILLSBORO-EXIT\captures\"

# Filtered/exported versions → exports\:
Move-Item "C:\Users\fletc\Cryptometer\<export>.pcapng" "E:\WireShark\BHN-US3-HILLSBORO-EXIT\exports\"

# Analysis notes → notes\:
Move-Item "C:\Users\fletc\Cryptometer\<notes>.txt" "E:\WireShark\BHN-US3-HILLSBORO-EXIT\notes\"

# Copy instead of move (keeps file in staging):
Copy-Item "C:\Users\fletc\Cryptometer\<file>.pcapng" "E:\WireShark\BHN-US3-HILLSBORO-EXIT\captures\"

# Rename on archive:
Move-Item "C:\Users\fletc\Cryptometer\<file>.pcapng" "E:\WireShark\BHN-US3-HILLSBORO-EXIT\captures\hillsboro-wireguard-test.pcapng"
```

---

## New Node Setup — First-Time Wireshark Prep

Run this on any new node before taking captures. For Helsinki, SSH to `46.62.162.87` (or `10.8.0.8` via WG after bootstrap):

```bash
# Install tshark (non-interactive — accept license automatically):
DEBIAN_FRONTEND=noninteractive apt-get install -y tshark

# Allow non-root users to capture (optional — we run as root so not strictly needed):
dpkg-reconfigure wireshark-common   # select Yes if prompted

# Create node-local folder structure:
NODE_FOLDER="BHN-EU1-HELSINKI-EXIT"   # change per node
mkdir -p /root/WireShark/${NODE_FOLDER}/{captures,exports,notes}
chmod 700 /root/WireShark

# Verify:
ls -la /root/WireShark/${NODE_FOLDER}/
tshark --version
```

**Node folder names for each server:**
```bash
# LA:        BHN-US1-LOSANGELES-HUB
# NJ:        BHN-US2-NEWJERSEY-HF
# Hillsboro: BHN-US3-HILLSBORO-EXIT
# Helsinki:  BHN-EU1-HELSINKI-EXIT
```

Test capture on Helsinki (30 seconds on the primary interface):
```bash
IFACE=$(ip route | awk '/^default/{print $5; exit}')
tshark -i "$IFACE" -a duration:30 \
  -w /root/WireShark/BHN-EU1-HELSINKI-EXIT/exports/helsinki-test-$(date +%Y%m%d-%H%M%S).pcapng
ls -lh /root/WireShark/BHN-EU1-HELSINKI-EXIT/exports/
```

---

## Notes

- SSH key for all SCP: `C:\Users\fletc\.ssh\id_ed25519`
- LA always requires `--noproxy '*'` for `curl` (global http_proxy set); not relevant for `scp`
- NJ always uses port `2222` — don't forget `-P 2222` on SCP
- BHN-BLACKBOX vault path: `\\cryptomator-vault\8TFkonLypcZg\BHN-BLACKBOX` (mapped as `E:`)
- Node-local captures are not automatically cleaned up — purge `/root/WireShark/*/captures/` on the node after archiving
