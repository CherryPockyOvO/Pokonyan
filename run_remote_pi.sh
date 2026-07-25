#!/usr/bin/env bash
# Automatically sync GitHub code to Raspberry Pi 5 via SSH, launch lightweight top.py --no-audio on Pi,
# and start local PC Audio Node.

PI_USER="xzm"
PI_HOST="Milos-Pi5.local"
PI_DIR="~/Pokonyan"

if [ -n "$1" ]; then
    PI_HOST="$1"
fi

echo "=========================================================="
echo "🚀 1. Syncing latest GitHub repo on Raspberry Pi ($PI_USER@$PI_HOST)..."
echo "=========================================================="

ssh "$PI_USER@$PI_HOST" "cd $PI_DIR && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio" &
PI_PID=$!

sleep 3

echo "=========================================================="
echo "🎙️ 2. Starting Local PC Audio Node (YAMNet + Whisper)..."
echo "=========================================================="

python3 pc_audio_client.py --pi-host "$PI_HOST"

kill $PI_PID 2>/dev/null
