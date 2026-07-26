#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone YAMNet Doorbell & Bell Sound Classification Tester.
純粹 YAMNet 門鈴/鈴聲實時聲音分類測試腳本 (無帶通濾波，直接讀取原始麥克風音訊推導)。
"""

import os
import sys
import time
import json
import argparse
import urllib.request
import numpy as np
from colorama import Fore, Style, init

init(autoreset=True)

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure sounddevice is installed
try:
    import sounddevice as sd
except ImportError:
    import subprocess
    print("Installing missing 'sounddevice'...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "sounddevice"])
    import sounddevice as sd

# Ensure LiteRT/TFLite interpreter is installed
try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        try:
            from tensorflow.lite import Interpreter
        except ImportError:
            import subprocess
            print("Installing missing 'ai-edge-litert' TFLite interpreter...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "ai-edge-litert"])
            from ai_edge_litert.interpreter import Interpreter

# AudioSet class ontology mapping (Doorbell / Bell / Ring / Alarm / Environmental)
DOORBELL_CLASSES = {
    349: "doorbell",
    350: "ding-dong",
    173: "bell",
    195: "church_bell",
    196: "jingle_bell",
    197: "bicycle_bell",
    198: "chime",
    200: "campanology",
    201: "carillon",
    202: "tubular_bells",
    384: "telephone_ring",
    385: "ringtone",
}

ALARM_CLASSES = {
    382: "alarm",
    389: "alarm_clock",
    390: "siren",
    391: "fire_alarm",
    393: "civil_defense_siren",
    394: "buzzer",
    395: "police_siren",
    396: "ambulance_siren",
    397: "fire_engine_siren",
}

GENERAL_CLASSES = {
    0: "speech",
    16: "laughter",
    45: "cough",
    48: "snore",
    51: "whistling",
    57: "applause",
    74: "dog_bark",
    81: "cat_meow",
    137: "music",
}

ALL_CLASS_NAMES = {**DOORBELL_CLASSES, **ALARM_CLASSES, **GENERAL_CLASSES}

def send_event_to_pi(pi_host, port, event, score):
    url = f"http://{pi_host}:{port}/trigger_audio_event"
    payload = json.dumps({"event": event, "score": float(score)}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Standalone YAMNet Doorbell & Alarm Sound Classifier")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--port", type=int, default=8080, help="Raspberry Pi web server port (default: 8080)")
    parser.add_argument("--model", default="model/yamnet.tflite", help="Path to yamnet.tflite model")
    parser.add_argument("--threshold", type=float, default=0.20, help="Detection threshold (default: 0.20)")
    args = parser.parse_args()

    model_path = os.path.abspath(args.model)
    if not os.path.exists(model_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(base_dir, "model", "yamnet.tflite")

    print(f"{Fore.CYAN}========================================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN} 🔔 Standalone YAMNet Doorbell & Alarm Sound Classifier  {Style.RESET_ALL}")
    print(f"{Fore.CYAN}========================================================{Style.RESET_ALL}")
    print(f"📦 YAMNet Model : {model_path}")
    print(f"📡 Target Pi    : http://{args.pi_host}:{args.port}/")
    print(f"🎯 Threshold    : {args.threshold:.2f}")
    print(f"💡 Three Categories: NORMAL | DOORBELL | ALARM\n")

    interp = Interpreter(model_path=model_path)
    input_details = interp.get_input_details()
    output_details = interp.get_output_details()

    sample_rate = 16000
    window_samples = 15600  # 0.975s for YAMNet
    interp.resize_tensor_input(input_details[0]["index"], [window_samples])
    interp.allocate_tensors()

    audio_buffer = np.zeros(window_samples, dtype=np.float32)

    def audio_callback(indata, frames, time_info, status):
        nonlocal audio_buffer
        mono = indata[:, 0]
        if len(mono) >= window_samples:
            audio_buffer[:] = mono[-window_samples:]
        else:
            audio_buffer = np.roll(audio_buffer, -len(mono))
            audio_buffer[-len(mono):] = mono

    stream = sd.InputStream(
        channels=1,
        samplerate=sample_rate,
        blocksize=int(sample_rate * 0.25),
        callback=audio_callback,
    )

    last_trigger_time = 0.0

    print(f"{Fore.GREEN}✅ Microphone listening... Press Ctrl+C to stop.{Style.RESET_ALL}\n")
    stream.start()

    try:
        while True:
            time.sleep(0.25)
            interp.set_tensor(input_details[0]["index"], audio_buffer)
            interp.invoke()
            scores = interp.get_tensor(output_details[0]["index"])[0]

            top_indices = np.argsort(scores)[::-1][:3]
            top_class = top_indices[0]
            top_score = scores[top_class]

            # 1. Check ALARM category
            alarm_hits = []
            for idx, label in ALARM_CLASSES.items():
                if idx < len(scores) and scores[idx] >= args.threshold:
                    alarm_hits.append((label, scores[idx]))

            # 2. Check DOORBELL category
            doorbell_hits = []
            for idx, label in DOORBELL_CLASSES.items():
                if idx < len(scores) and scores[idx] >= args.threshold:
                    doorbell_hits.append((label, scores[idx]))

            now = time.monotonic()
            top_name = ALL_CLASS_NAMES.get(top_class, f"class_{top_class}")

            if alarm_hits:
                alarm_hits.sort(key=lambda x: x[1], reverse=True)
                name, score = alarm_hits[0]
                print(f"\r{Fore.RED}🚨 [ALARM DETECTED] {name} ({score:.2f}) {Style.RESET_ALL}")
                if now - last_trigger_time >= 0.4:
                    last_trigger_time = now
                    send_event_to_pi(args.pi_host, args.port, name, score)

            elif doorbell_hits:
                doorbell_hits.sort(key=lambda x: x[1], reverse=True)
                name, score = doorbell_hits[0]
                print(f"\r{Fore.YELLOW}🔔 [DOORBELL DETECTED] {name} ({score:.2f}) {Style.RESET_ALL}")
                if now - last_trigger_time >= 0.4:
                    last_trigger_time = now
                    send_event_to_pi(args.pi_host, args.port, name, score)

            else:
                if top_score >= 0.15:
                    print(f"\r[YAMNet Live] Sound: {top_name} ({top_score:.2f})      ", end="", flush=True)
                    if now - last_trigger_time >= 0.4:
                        last_trigger_time = now
                        send_event_to_pi(args.pi_host, args.port, top_name, top_score)

    except KeyboardInterrupt:
        print("\nStopping YAMNet Classifier...")
        stream.stop()
        stream.close()
        print("YAMNet Classifier Stopped.")

if __name__ == "__main__":
    main()
