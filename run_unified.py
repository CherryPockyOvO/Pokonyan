#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified Master Launcher for Pokonyan.
Runs all 3 nodes (Raspberry Pi SSH, C++ GPU STT, Python YAMNet) in a SINGLE terminal window
with color-coded log prefixing!
"""

import os
import sys
import time
import subprocess
import threading
import argparse

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLOR_PI = Fore.CYAN
    COLOR_CPP = Fore.GREEN
    COLOR_YAMNET = Fore.MAGENTA
    COLOR_SYS = Fore.YELLOW
    COLOR_RESET = Style.RESET_ALL
except ImportError:
    COLOR_PI = ""
    COLOR_CPP = ""
    COLOR_YAMNET = ""
    COLOR_SYS = ""
    COLOR_RESET = ""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def stream_output(process, prefix, color):
    for line in iter(process.stdout.readline, ''):
        if line:
            line_str = line.strip()
            if line_str:
                print(f"{color}[{prefix}]{COLOR_RESET} {line_str}")

def main():
    parser = argparse.ArgumentParser(description="Pokonyan Single-Terminal Unified Launcher")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    args = parser.parse_args()

    print(f"{COLOR_SYS}========================================================{COLOR_RESET}")
    print(f"{COLOR_SYS} 🤖 Pokonyan Single-Terminal Unified Dashboard Launcher  {COLOR_RESET}")
    print(f"{COLOR_SYS}========================================================{COLOR_RESET}")
    print(f"📡 Target Raspberry Pi: http://{args.pi_host}:8080/")
    print(f"💡 All 3 nodes running in this SINGLE terminal window!\n")

    cpp_dir = os.path.join(BASE_DIR, "cpp_audio_client")
    cpp_exe = os.path.join(cpp_dir, "build", "Release", "cpp_audio_client.exe")
    model_file = os.path.join(cpp_dir, "models", "ggml-base.bin")

    # Command 1: Raspberry Pi SSH
    cmd_pi = ["ssh", "-t", f"xzm@{args.pi_host}", "cd ~/Pokonyan && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio"]

    # Command 2: C++ GPU STT Client
    if os.path.exists(cpp_exe):
        cmd_cpp = [cpp_exe, "--pi-host", args.pi_host, "-m", model_file, "-l", "en"]
    else:
        cmd_cpp = ["cmd.exe", "/c", f"cd /d {cpp_dir} && call build_and_run.bat {args.pi_host}"]

    # Command 3: Python YAMNet Classifier
    cmd_yamnet = [sys.executable, os.path.join(BASE_DIR, "pc_audio_client.py"), "--pi-host", args.pi_host]

    procs = []

    print(f"{COLOR_SYS}[System] Starting Node 1: Raspberry Pi 5 SSH...{COLOR_RESET}")
    p1 = subprocess.Popen(cmd_pi, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=BASE_DIR)
    t1 = threading.Thread(target=stream_output, args=(p1, "🤖 Pi", COLOR_PI), daemon=True)
    t1.start()
    procs.append(p1)

    print(f"{COLOR_SYS}[System] Starting Node 2: C++ GPU STT Client...{COLOR_RESET}")
    p2 = subprocess.Popen(cmd_cpp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=cpp_dir)
    t2 = threading.Thread(target=stream_output, args=(p2, "⚡ C++ GPU", COLOR_CPP), daemon=True)
    t2.start()
    procs.append(p2)

    print(f"{COLOR_SYS}[System] Starting Node 3: Python YAMNet Classifier...{COLOR_RESET}")
    p3 = subprocess.Popen(cmd_yamnet, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=BASE_DIR)
    t3 = threading.Thread(target=stream_output, args=(p3, "🔔 YAMNet", COLOR_YAMNET), daemon=True)
    t3.start()
    procs.append(p3)

    print(f"\n{COLOR_GREEN if 'COLOR_GREEN' in globals() else ''}✅ All 3 nodes successfully running in this single window! Press Ctrl+C to stop.{COLOR_RESET}\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n{COLOR_SYS}[System] Stopping all 3 nodes...{COLOR_RESET}")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        print(f"{COLOR_SYS}[System] Exited successfully.{COLOR_RESET}")

if __name__ == "__main__":
    main()
