#!/usr/bin/env bash
# Simple Interactive SSH Launcher for Raspberry Pi 5

PI_USER="xzm"
PI_HOST="100.80.242.72"
PI_DIR="~/Pokonyan"

if [ -n "$1" ]; then
    PI_HOST="$1"
fi

echo "=========================================================="
echo "🚀 Connecting to Raspberry Pi ($PI_USER@$PI_HOST)..."
echo "=========================================================="

# 使用 pkill ... || true 避免無進程時 exit code 1 導致 SSH 中斷關閉
ssh -t "$PI_USER@$PI_HOST" "bash -c 'pkill -9 -f top.py 2>/dev/null || true; cd $PI_DIR && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio --dry-run'"
