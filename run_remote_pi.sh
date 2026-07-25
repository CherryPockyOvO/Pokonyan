#!/usr/bin/env bash
# Direct Launcher: Sync code to Raspberry Pi 5 via SSH, kill stale processes, and run top.py with Web UI on port 8080

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

# 1. 優先 pkill 清理上一次未完全關閉的 top.py 進程，強制釋放相機 /dev/media0 與 8080 端口
# 2. 同步最新倉庫並啟動 top.py
$SSH_CMD "$PI_USER@$PI_HOST" "pkill -9 -f top.py 2>/dev/null; sleep 1; cd $PI_DIR && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio --dry-run"
