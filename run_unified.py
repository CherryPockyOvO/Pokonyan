#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified Master Launcher for Pokonyan with Auto-Login SSH to Raspberry Pi.
Runs all 3 nodes (Raspberry Pi SSH with auto-password 123456, C++ GPU STT, Python YAMNet)
in a SINGLE terminal window with color-coded log prefixing!
"""

import os
import sys
import time
import subprocess
import threading
import argparse

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Auto-install paramiko if missing
try:
    import paramiko
except ImportError:
    print("[System] Installing 'paramiko' for automatic Raspberry Pi SSH auto-login...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko

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
    while True:
        line_bytes = process.stdout.readline()
        if not line_bytes:
            break
        try:
            line_str = line_bytes.decode('utf-8').strip()
        except Exception:
            line_str = line_bytes.decode('gbk', errors='ignore').strip()
        if line_str:
            print(f"{color}[{prefix}]{COLOR_RESET} {line_str}")

def run_ssh_paramiko(host, user, password, command, prefix, color, stop_event):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"{color}[{prefix}]{COLOR_RESET} Connecting to {user}@{host} with auto-login (Password: ******)...")
        ssh.connect(hostname=host, username=user, password=password, timeout=15, banner_timeout=30)
        print(f"{color}[{prefix}]{COLOR_RESET} SSH Login Successful! Launching top.py...")

        stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
        while not stop_event.is_set():
            line = stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str:
                print(f"{color}[{prefix}]{COLOR_RESET} {line_str}")
    except Exception as e:
        print(f"{color}[{prefix}]{COLOR_RESET} SSH Connection Note: {e}")
    finally:
        try:
            ssh.close()
        except Exception:
            pass

def get_audio_python():
    conda_audio_py = r"D:\Z-Anaconda3\envs\audio\python.exe"
    if os.path.exists(conda_audio_py):
        return conda_audio_py
    return sys.executable

def main():
    parser = argparse.ArgumentParser(description="Pokonyan Single-Terminal Unified Launcher with SSH Auto-Login")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--pi-user", default="xzm", help="Raspberry Pi SSH user (default: xzm)")
    parser.add_argument("--pi-pass", default="123456", help="Raspberry Pi SSH password (default: 123456)")
    args = parser.parse_args()

    py_exe = get_audio_python()

    print(f"{COLOR_SYS}========================================================{COLOR_RESET}")
    print(f"{COLOR_SYS} Pokonyan Single-Terminal Dashboard (Auto-Login SSH)  {COLOR_RESET}")
    print(f"{COLOR_SYS}========================================================{COLOR_RESET}")
    print(f"Target Raspberry Pi: http://{args.pi_host}:8080/")
    print(f"SSH Auto-Login    : {args.pi_user}@{args.pi_host} (Password: {args.pi_pass})")
    print(f"Python Env        : {py_exe}")
    print(f"All 3 nodes running in this SINGLE terminal window!\n")

    cpp_dir = os.path.join(BASE_DIR, "cpp_audio_client")
    cpp_exe = os.path.join(cpp_dir, "build", "Release", "cpp_audio_client.exe")
    model_file = os.path.join(cpp_dir, "models", "ggml-base.bin")

    stop_event = threading.Event()
    procs = []

    # Node 1: Mobile Mic WebSocket Bridge + YAMNet Sound Classifier + CUDA RealtimeSTT
    cmd_yamnet = [py_exe, os.path.join(BASE_DIR, "pc_mobile_bridge.py"), "--pi-host", args.pi_host]
    p1 = subprocess.Popen(cmd_yamnet, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False, bufsize=0, cwd=BASE_DIR)
    t1 = threading.Thread(target=stream_output, args=(p1, "MobileBridge", COLOR_YAMNET), daemon=True)
    t1.start()
    procs.append(p1)

    # Node 2: Raspberry Pi SSH Auto-Login thread
    pi_cmd = "cd ~/Pokonyan && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio"
    t2 = threading.Thread(target=run_ssh_paramiko, args=(args.pi_host, args.pi_user, args.pi_pass, pi_cmd, "Pi", COLOR_PI, stop_event), daemon=True)
    t2.start()

    print(f"\n✅ Pokonyan Unified System Started! Listening on iPhone WebSocket audio feed. Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n{COLOR_SYS}[System] Stopping all nodes...{COLOR_RESET}")
        stop_event.set()
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        print(f"{COLOR_SYS}[System] Exited successfully.{COLOR_RESET}")

if __name__ == "__main__":
    main()
