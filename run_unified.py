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
    for line in iter(process.stdout.readline, ''):
        if line:
            line_str = line.strip()
            if line_str:
                print(f"{color}[{prefix}]{COLOR_RESET} {line_str}")

def run_ssh_paramiko(host, user, password, command, prefix, color, stop_event):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"{color}[{prefix}]{COLOR_RESET} Connecting to {user}@{host} with auto-login (Password: ******)...")
        ssh.connect(hostname=host, username=user, password=password, timeout=10)
        print(f"{color}[{prefix}]{COLOR_RESET} 🔑 SSH Login Successful! Launching top.py...")

        channel = ssh.get_transport().open_session()
        channel.get_pty()
        channel.exec_command(command)

        buf = ""
        while not stop_event.is_set():
            if channel.recv_ready():
                data = channel.recv(1024).decode('utf-8', errors='ignore')
                buf += data
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line_str = line.strip()
                    if line_str:
                        print(f"{color}[{prefix}]{COLOR_RESET} {line_str}")
            elif channel.exit_status_ready():
                break
            time.sleep(0.05)
    except Exception as e:
        print(f"{color}[{prefix}]{COLOR_RESET} SSH Connection Error: {e}")
    finally:
        try:
            ssh.close()
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser(description="Pokonyan Single-Terminal Unified Launcher with SSH Auto-Login")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--pi-user", default="xzm", help="Raspberry Pi SSH user (default: xzm)")
    parser.add_argument("--pi-pass", default="123456", help="Raspberry Pi SSH password (default: 123456)")
    args = parser.parse_args()

    print(f"{COLOR_SYS}========================================================{COLOR_RESET}")
    print(f"{COLOR_SYS} 🤖 Pokonyan Single-Terminal Dashboard (Auto-Login SSH)  {COLOR_RESET}")
    print(f"{COLOR_SYS}========================================================{COLOR_RESET}")
    print(f"📡 Target Raspberry Pi: http://{args.pi_host}:8080/")
    print(f"🔑 SSH Auto-Login    : {args.pi_user}@{args.pi_host} (Password: {args.pi_pass})")
    print(f"💡 All 3 nodes running in this SINGLE terminal window!\n")

    cpp_dir = os.path.join(BASE_DIR, "cpp_audio_client")
    cpp_exe = os.path.join(cpp_dir, "build", "Release", "cpp_audio_client.exe")
    model_file = os.path.join(cpp_dir, "models", "ggml-base.bin")

    stop_event = threading.Event()
    procs = []

    # Node 1: Raspberry Pi SSH Auto-Login thread
    pi_cmd = "cd ~/Pokonyan && git fetch origin main && git reset --hard origin/main && python3 top.py --no-audio"
    t1 = threading.Thread(target=run_ssh_paramiko, args=(args.pi_host, args.pi_user, args.pi_pass, pi_cmd, "🤖 Pi", COLOR_PI, stop_event), daemon=True)
    t1.start()

    # Node 2: C++ GPU STT Client
    if os.path.exists(cpp_exe):
        cmd_cpp = [cpp_exe, "--pi-host", args.pi_host, "-m", model_file, "-l", "en"]
    else:
        cmd_cpp = ["cmd.exe", "/c", f"cd /d {cpp_dir} && call build_and_run.bat {args.pi_host}"]

    p2 = subprocess.Popen(cmd_cpp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=cpp_dir)
    t2 = threading.Thread(target=stream_output, args=(p2, "⚡ C++ GPU", COLOR_CPP), daemon=True)
    t2.start()
    procs.append(p2)

    # Node 3: Python YAMNet Classifier
    cmd_yamnet = [sys.executable, os.path.join(BASE_DIR, "pc_audio_client.py"), "--pi-host", args.pi_host]
    p3 = subprocess.Popen(cmd_yamnet, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=BASE_DIR)
    t3 = threading.Thread(target=stream_output, args=(p3, "🔔 YAMNet", COLOR_YAMNET), daemon=True)
    t3.start()
    procs.append(p3)

    print(f"\n✅ All 3 nodes successfully initialized with automatic login! Press Ctrl+C to stop.\n")

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
