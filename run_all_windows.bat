@echo off
setlocal enabledelayedexpansion

set "PI_HOST=100.80.242.72"
if not "%~1"=="" set "PI_HOST=%~1"

echo ==========================================================
echo  Pokonyan Single-Window Launcher Option
echo ==========================================================
echo Target Pi Host: %PI_HOST%
echo.

rem Check if Windows Terminal (wt.exe) is available
where wt.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [Info] Launching Windows Terminal with 3 Split Panes in ONE Window...
    wt -M -d "%~dp0" --title "🤖 Raspberry Pi" cmd /k "call run_remote_pi.bat %PI_HOST%" ; split-pane -H -d "%~dp0cpp_audio_client" --title "⚡ C++ GPU STT" cmd /k "call build_and_run.bat %PI_HOST%" ; split-pane -V -d "%~dp0" --title "🔔 YAMNet" cmd /k "python pc_audio_client.py --pi-host %PI_HOST%"
    exit /b 0
)

echo [Info] Running in Single Unified Terminal Mode...
python run_unified.py --pi-host %PI_HOST%

pause
