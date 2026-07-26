@echo off
setlocal enabledelayedexpansion

set "PI_HOST=100.80.242.72"
if not "%~1"=="" set "PI_HOST=%~1"

echo ==========================================================
echo  Launching Pokonyan Dual System (Raspberry Pi + Windows GPU STT)
echo ==========================================================
echo Target Pi Host: %PI_HOST%
echo.

echo [1/2] Starting Raspberry Pi 5 Top Node via SSH...
start "Pokonyan - Raspberry Pi Remote Node" cmd /k "call run_remote_pi.bat %PI_HOST%"

echo [2/2] Starting Windows C++ GPU Audio STT Client...
start "Pokonyan - Windows C++ GPU Audio Client" cmd /k "cd cpp_audio_client && call build_and_run.bat %PI_HOST%"

echo.
echo Both systems started! Check the two open terminal windows.
echo Raspberry Pi Web UI: http://%PI_HOST%:8080/
echo.
pause
