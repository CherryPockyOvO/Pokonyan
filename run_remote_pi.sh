#!/usr/bin/env bash
# Interactive SSH Launcher for Raspberry Pi 5

PI_USER="xzm"
PI_HOST="100.80.242.72"
PI_DIR="~/Pokonyan"

if [ -n "$1" ]; then
    PI_HOST="$1"
fi

echo "=========================================================="
echo "🚀 Connecting to Raspberry Pi ($PI_USER@$PI_HOST)..."
echo "=========================================================="

# 直接執行 git reset 與 python3 top.py，無任何 pkill 邏輯
ssh -t "$PI_USER@$PI_HOST" "cd $PI_DIR && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio"
