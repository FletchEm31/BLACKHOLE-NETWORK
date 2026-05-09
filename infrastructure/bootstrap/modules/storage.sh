#!/bin/bash
# infrastructure/bootstrap/modules/storage.sh
#
# Sourced by eh-node-bootstrap.sh. Provides:
#   setup_encrypted_storage <device> <luks_name> <mountpoint> [subdir1 subdir2 ...]
#
# Encrypts a block device with LUKS2 (if not already), formats XFS, mounts at
# the requested mountpoint, persists via /etc/crypttab + /etc/fstab, creates
# the requested subdirectories. Auto-generates a 4 KiB random keyfile under
# /root/.luks-<luks_name> the first time it runs.
#
# Idempotent: skips destructive steps if device is already a LUKS volume.
# WARNING: first-run formatting destroys any existing data on <device>.

setup_encrypted_storage() {
  local device="$1" luks_name="$2" mountpoint="$3"
  shift 3
  local subdirs=("$@")

  if [[ ! -b "$device" ]]; then
    warn "Device $device not present — skipping ${luks_name}"
    return 0
  fi

  log "Encrypted storage: ${luks_name} ($device → $mountpoint)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cryptsetup xfsprogs

  local keyfile="/root/.luks-${luks_name}"
  if [[ ! -f "$keyfile" ]]; then
    dd if=/dev/urandom of="$keyfile" bs=512 count=8 status=none
    chmod 600 "$keyfile"
    ok "LUKS keyfile created at $keyfile"
  fi

  if ! cryptsetup isLuks "$device" 2>/dev/null; then
    warn "Formatting $device with LUKS2 — destroys existing data"
    cryptsetup luksFormat --type luks2 --batch-mode --key-file="$keyfile" "$device"
  fi

  cryptsetup open "$device" "$luks_name" --key-file="$keyfile" 2>/dev/null || true

  if ! blkid "/dev/mapper/${luks_name}" 2>/dev/null | grep -q xfs; then
    mkfs.xfs -f "/dev/mapper/${luks_name}"
  fi

  mkdir -p "$mountpoint"
  mountpoint -q "$mountpoint" || \
    mount -o noatime,nodiratime "/dev/mapper/${luks_name}" "$mountpoint"

  # Persistence
  local uuid
  uuid="$(blkid -s UUID -o value "$device")"
  grep -q "^${luks_name} " /etc/crypttab || \
    echo "${luks_name} UUID=${uuid} ${keyfile} luks" >>/etc/crypttab
  grep -q " ${mountpoint} " /etc/fstab || \
    echo "/dev/mapper/${luks_name} ${mountpoint} xfs defaults,noatime,nodiratime,nofail 0 0" >>/etc/fstab

  for d in "${subdirs[@]}"; do
    mkdir -p "${mountpoint}/${d}"
  done

  ok "${luks_name} mounted at ${mountpoint}"
}
