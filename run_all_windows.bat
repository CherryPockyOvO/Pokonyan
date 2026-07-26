@echo off
set "PI_HOST=100.80.242.72"
if not "%~1"=="" set "PI_HOST=%~1"

echo ==========================================================
echo  Launching Pokonyan Single-Terminal Unified Dashboard
echo ==========================================================
echo Target Pi Host: %PI_HOST%
echo.

python run_unified.py --pi-host %PI_HOST%

pause
