#!/usr/bin/env bash
# 给小内存机器加 swap（幂等）。用法: setup-swap.sh [size, 默认 4G]
set -euo pipefail
SIZE="${1:-4G}"
SWAPFILE=/swapfile

if swapon --show | grep -q "$SWAPFILE"; then
  echo "swap already on:"; swapon --show; exit 0
fi

fallocate -l "$SIZE" "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count="$(( ${SIZE%G} * 1024 ))"
chmod 600 "$SWAPFILE"
mkswap "$SWAPFILE"
swapon "$SWAPFILE"
grep -q "$SWAPFILE" /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
sysctl -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

echo "swap enabled:"; swapon --show; free -m
