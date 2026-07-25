#!/usr/bin/env bash
# Direct Launcher: Sync code to Raspberry Pi 5 via SSH and run top.py with Web UI on port 8080 (--no-audio)

PI_USER="xzm"
PI_HOST="100.80.242.72"
PI_PASS="123456"
PI_DIR="~/Pokonyan"

if [ -n "$1" ]; then
    PI_HOST="$1"
fi

echo "=========================================================="
echo "🚀 Syncing GitHub repo and launching Web Server on Pi ($PI_USER@$PI_HOST)..."
echo "=========================================================="

SSH_CMD="ssh"
if command -v sshpass >/dev/null 2>&1; then
    SSH_CMD="sshpass -p $PI_PASS ssh"
fi

# 執行 SSH 命令，顯示完整樹莓派輸出 logs
$SSH_CMD "$PI_USER@$PI_HOST" "cd $PI_DIR && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio --dry-run"
