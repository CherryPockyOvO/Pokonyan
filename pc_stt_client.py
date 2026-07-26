# -*- coding: utf-8 -*-
"""Real-Time Speech Transcription Node (RealtimeSTT CUDA GPU).
Uses RealtimeSTT AudioToTextRecorder on CUDA GPU (tiny.en + small.en).
Outputs completed sentences in alternating CYAN / YELLOW colors,
and pushes 100% completed sentences to Raspberry Pi over HTTP POST.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import subprocess
from colorama import Fore, Style, init

# Ensure RealtimeSTT package path is available
rt_dir = r"c:\Users\ZgZhi\Desktop\RealtimeSTT"
if os.path.exists(rt_dir) and rt_dir not in sys.path:
    sys.path.insert(0, rt_dir)

try:
    from RealtimeSTT import AudioToTextRecorder
except ImportError:
    print("[STT] Installing RealtimeSTT package...")
    if os.path.exists(rt_dir):
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", rt_dir])
    else:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "RealtimeSTT"])
    from RealtimeSTT import AudioToTextRecorder

init(autoreset=True)

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def send_transcript_to_pi(pi_host, port, text):
    url = f"http://{pi_host}:{port}/transcribe_text"
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            print(f"[STT -> Pi {pi_host}] Sent: '{text}'")
            return True
    except Exception as e:
        print(f"[STT -> Pi {pi_host}] Failed to send transcript: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="RealtimeSTT CUDA GPU Client for Pokonyan")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--port", type=int, default=8080, help="Raspberry Pi web server port (default: 8080)")
    parser.add_argument("-m", "--model", default="small.en", help="Final model size (default: small.en)")
    parser.add_argument("-r", "--rt-model", default="tiny.en", help="Realtime draft model size (default: tiny.en)")
    parser.add_argument("-l", "--lang", default="en", help="Language (default: en)")
    args = parser.parse_args()

    sentence_count = 0

    def process_text(text):
        nonlocal sentence_count
        text = text.strip()
        if not text:
            return

        sentence_count += 1
        # Alternate color between Cyan and Yellow for completed sentences
        color = Fore.CYAN if sentence_count % 2 == 1 else Fore.YELLOW
        print(f"{color}[Completed Sentence #{sentence_count}] {text}{Style.RESET_ALL}")
        
        # Push 100% completed sentence to Raspberry Pi Web UI & top.py
        send_transcript_to_pi(args.pi_host, args.port, text)

    recorder_config = {
        'model': args.model,
        'realtime_model_type': args.rt_model,
        'language': args.lang,
        'device': 'cuda',
        'compute_type': 'float16',
        'enable_realtime_transcription': True,
        'realtime_processing_pause': 0.15,
        'post_speech_silence_duration': 0.6,
        'min_length_of_recording': 0.5,
        'spinner': False,
    }

    print(f"[RealtimeSTT GPU] Initializing CUDA GPU models ({args.rt_model} + {args.model})...")
    recorder = AudioToTextRecorder(**recorder_config)
    print(f"[RealtimeSTT GPU] Listening on microphone... (Speak into mic)")

    try:
        while True:
            recorder.text(process_text)
    except KeyboardInterrupt:
        print("[RealtimeSTT GPU] Stopped by user.")
        sys.exit(0)

if __name__ == '__main__':
    main()
