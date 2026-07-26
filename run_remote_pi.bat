@echo off
setlocal enabledelayedexpansion

set "PI_USER=xzm"
set "PI_HOST=100.80.242.72"
set "PI_DIR=~/Pokonyan"

if not "%~1"=="" set "PI_HOST=%~1"

echo ==========================================================
echo  Connecting to Raspberry Pi (%PI_USER%@%PI_HOST%)...
echo ==========================================================

ssh -t %PI_USER%@%PI_HOST% "cd %PI_DIR% && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio"

pause
