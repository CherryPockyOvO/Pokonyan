@echo off
setlocal enabledelayedexpansion

set "PI_HOST=100.80.242.72"
if not "%~1"=="" set "PI_HOST=%~1"

echo ==========================================================
echo  Launching Pokonyan Full System (Raspberry Pi + C++ GPU STT + Python YAMNet)
echo ==========================================================
echo Target Pi Host: %PI_HOST%
echo.

echo [1/3] Starting Raspberry Pi 5 Top Node via SSH...
start "Pokonyan - Raspberry Pi Remote Node" cmd /k "call run_remote_pi.bat %PI_HOST%"

echo [2/3] Starting Windows C++ GPU Speech STT Client...
start "Pokonyan - Windows C++ GPU STT Client" cmd /k "cd cpp_audio_client && call build_and_run.bat %PI_HOST%"

echo [3/3] Starting Windows Python YAMNet Sound Classifier...
start "Pokonyan - Python YAMNet Sound Classifier" cmd /k "python pc_audio_client.py --pi-host %PI_HOST%"

echo.
echo All 3 nodes launched! Check the three open terminal windows.
echo Raspberry Pi Web UI: http://%PI_HOST%:8080/
echo.
pause
