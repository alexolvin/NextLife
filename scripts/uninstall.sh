#!/usr/bin/env bash
# NextLife uninstaller — run as root: sudo make uninstall
# Reverts the install: stops/disables services, removes units + nft table +
# /opt/nextlife. The data dir /var/lib/nextlife is KEPT (it holds data and is
# the home of the `nextlife` user); remove it manually if you want a full wipe.
set -euo pipefail

OPT_DIR="/opt/nextlife"
DATA_DIR="/var/lib/nextlife"

log() { echo "[uninstall] $*"; }

log "Stopping and disabling services"
systemctl stop nextlife-web 2>/dev/null || true
systemctl stop nextlife-nft 2>/dev/null || true
systemctl disable nextlife-web 2>/dev/null || true
systemctl disable nextlife-nft 2>/dev/null || true
rm -f /etc/systemd/system/nextlife-web.service
rm -f /etc/systemd/system/nextlife-nft.service
systemctl daemon-reload

log "Removing nft rules"
nft delete table inet nextlife 2>/dev/null || true
rm -f /etc/nftables/nextlife.nft

log "Removing $OPT_DIR"
rm -rf "$OPT_DIR"

log "KEPT: $DATA_DIR (data + home of user nextlife). Manual wipe: rm -rf $DATA_DIR"
log "Uninstall complete."
