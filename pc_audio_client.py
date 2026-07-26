# -*- coding: utf-8 -*-
"""Local PC Audio Client for Offloading YAMNet/Whisper from Raspberry Pi.

Runs locally on your Mac/PC, captures local microphone audio, runs YAMNet/Whisper,
and sends triggered audio events directly to your Raspberry Pi 5 over HTTP POST.
"""

import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
import subprocess

try:
    import sounddevice
except ImportError:
    print("[PC Audio] Installing missing 'sounddevice' package...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "sounddevice"])

# Ensure local perception directory can be imported
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from perception.audio_pipeline import YamnetWhisperAudioPipeline


if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def local_path(value):
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    p1 = BASE_DIR / path
    if p1.exists():
        return p1
    p2 = BASE_DIR / "model" / path
    if p2.exists():
        return p2
    if not str(path).startswith("model/"):
        return BASE_DIR / "model" / path
    return p1


def send_event_to_pi(pi_host, port, event, score):
    url = f"http://{pi_host}:{port}/trigger_audio_event"
    payload = json.dumps({"event": event, "score": score}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = resp.read()
            print(f"[PC Audio -> Pi {pi_host}] Event '{event}' ({score:.2f}) sent successfully!")
            return True
    except Exception as e:
        print(f"[PC Audio -> Pi {pi_host}] Failed to send event '{event}': {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi hostname or IP (default: 100.80.242.72)")
    parser.add_argument("--port", type=int, default=8080, help="Raspberry Pi web server port (default: 8080)")
    parser.add_argument("--yamnet", default="model/yamnet.tflite")
    parser.add_argument("--whisper", default="model/ggml-tiny.en.bin")
    parser.add_argument("--speech-thresh", type=float, default=0.35)
    parser.add_argument("--event-thresh", type=float, default=0.45)
    args = parser.parse_args()

    print(f"[PC Audio] Local PC Audio Node starting...")
    print(f"[PC Audio] Target Raspberry Pi: http://{args.pi_host}:{args.port}/")

    def on_pc_audio_event(event, score):
        print(f"[PC Audio] YAMNet detected sound: {event} ({score:.2f})")
        if event in ("alarm", "alarm_clock", "doorbell", "bell", "ring", "siren"):
            print(f"[RED ALERT] Bell/Alarm sound detected: '{event}' ({score:.2f}) -> Pushing to Pi!")
            send_event_to_pi(args.pi_host, args.port, event, score)

    audio = YamnetWhisperAudioPipeline(
        yamnet_model_path=local_path(args.yamnet),
        whisper_model_path=local_path(args.whisper),
        speech_threshold=args.speech_thresh,
        event_threshold=args.event_thresh,
        on_event=on_pc_audio_event,
    )
    audio.start()
    print("[PC Audio] Microphone listening... Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping PC Audio Client...")
    finally:
        audio.stop()
        print("PC Audio Client Stopped.")


if __name__ == "__main__":
    main()
