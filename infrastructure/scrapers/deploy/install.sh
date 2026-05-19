#!/bin/bash
# Install / update the bhn-cgc-pop-refresh weekly job on the LA hub.
# Idempotent — safe to re-run after updates to the scraper code.
#
# Run as root on LA:
#   bash install.sh
#
# Layout produced:
#   /opt/bhn/cgc-pop-scraper/       - code (cgc-pop-scrape.js, cgc-pop-scrape-all.js,
#                                      cgc-pop-load.js, sets.json)
#   /var/lib/bhn-cgc-pop/           - per-run JSON output
#   /usr/local/bin/bhn-cgc-pop-refresh   - wrapper script
#   /etc/systemd/system/bhn-cgc-pop-refresh.{service,timer}

set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "install.sh must run as root"; exit 1; fi

# This script is expected to be in the same directory as the source files (after scp).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${HERE%/deploy}"

install -d -m 0755 /opt/bhn/cgc-pop-scraper
install -d -m 0755 /var/lib/bhn-cgc-pop

install -m 0644 "$SRC/cgc-pop-scrape.js"     /opt/bhn/cgc-pop-scraper/
install -m 0644 "$SRC/cgc-pop-scrape-all.js" /opt/bhn/cgc-pop-scraper/
install -m 0644 "$SRC/cgc-pop-load.js"       /opt/bhn/cgc-pop-scraper/
install -m 0644 "$SRC/sets.json"             /opt/bhn/cgc-pop-scraper/

install -m 0755 "$HERE/bhn-cgc-pop-refresh"            /usr/local/bin/bhn-cgc-pop-refresh
install -m 0644 "$HERE/bhn-cgc-pop-refresh.service"    /etc/systemd/system/bhn-cgc-pop-refresh.service
install -m 0644 "$HERE/bhn-cgc-pop-refresh.timer"      /etc/systemd/system/bhn-cgc-pop-refresh.timer

systemctl daemon-reload
systemctl enable --now bhn-cgc-pop-refresh.timer
systemctl list-timers --all bhn-cgc-pop-refresh.timer --no-pager

echo
echo "installed. fire a one-shot run now with:"
echo "  systemctl start bhn-cgc-pop-refresh.service"
echo "  journalctl -u bhn-cgc-pop-refresh.service -f"
