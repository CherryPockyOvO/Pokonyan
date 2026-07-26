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
DOORBELL_BELL_CLASSES = {
    349: "Doorbell (門鈴)",
    350: "Ding-dong (叮咚門鈴)",
    173: "Bell (響鈴)",
    195: "Church bell (教堂大鐘)",
    196: "Jingle bell (叮噹鈴)",
    197: "Bicycle bell (腳踏車車鈴)",
    198: "Chime (風鈴/鐘聲)",
    200: "Campanology (連環鐘聲)",
    201: "Carillon (組鐘)",
    202: "Tubular bells (管鐘)",
    384: "Telephone bell ring (電話鈴聲)",
    385: "Ringtone (手機響鈴)",
    382: "Alarm (警報器)",
    389: "Alarm clock (鬧鐘)",
    390: "Siren (警笛/警報)",
    391: "Smoke detector / Fire alarm (火災煙霧警報器)",
    393: "Civil defense siren (防空警報)",
    394: "Buzzer (蜂鳴器/電鈴)",
    395: "Police siren (警車警笛)",
    396: "Ambulance siren (救護車警笛)",
    397: "Fire engine siren (消防車警笛)",
}

GENERAL_CLASSES = {
    0: "Speech (說話聲)",
    16: "Laughter (笑聲)",
    45: "Cough (咳嗽聲)",
    48: "Snore (打呼聲)",
    51: "Whistling (吹口哨)",
    57: "Applause (掌聲)",
    74: "Dog bark (狗吠)",
    81: "Cat meow (貓叫)",
    137: "Music (音樂聲)",
}

# Merge all known maps
ALL_CLASS_NAMES = {**DOORBELL_BELL_CLASSES, **GENERAL_CLASSES}

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
    parser = argparse.ArgumentParser(description="Standalone YAMNet Doorbell Sound Classifier")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--port", type=int, default=8080, help="Raspberry Pi web server port (default: 8080)")
    parser.add_argument("--model", default="model/yamnet.tflite", help="Path to yamnet.tflite model")
    parser.add_argument("--threshold", type=float, default=0.20, help="Doorbell detection confidence threshold (default: 0.20)")
    args = parser.parse_args()

    model_path = os.path.abspath(args.model)
    if not os.path.exists(model_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(base_dir, "model", "yamnet.tflite")

    print(f"{Fore.CYAN}========================================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN} 🔔 Standalone YAMNet Doorbell & Bell Sound Classifier  {Style.RESET_ALL}")
    print(f"{Fore.CYAN}========================================================{Style.RESET_ALL}")
    print(f"📦 YAMNet Model : {model_path}")
    print(f"📡 Target Pi    : http://{args.pi_host}:{args.port}/")
    print(f"🎯 Threshold    : {args.threshold:.2f}")
    print(f"💡 No bandpass filters, pure raw microphone classification!\n")

    # Initialize YAMNet TFLite Interpreter
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
        blocksize=int(sample_rate * 0.25),  # Update every 0.25s
        callback=audio_callback,
    )

    last_trigger_time = 0.0

    print(f"{Fore.GREEN}✅ Microphone listening... Press Ctrl+C to stop.{Style.RESET_ALL}\n")
    stream.start()

    try:
        while True:
            time.sleep(0.25)
            # Run YAMNet inference on raw audio_buffer
            interp.set_tensor(input_details[0]["index"], audio_buffer)
            interp.invoke()
            scores = interp.get_tensor(output_details[0]["index"])[0]

            # Find top predicted classes
            top_indices = np.argsort(scores)[::-1][:3]
            top_class = top_indices[0]
            top_score = scores[top_class]

            # Check if any doorbell/bell class index is triggered above threshold
            bell_hits = []
            for idx, label in DOORBELL_BELL_CLASSES.items():
                if idx < len(scores) and scores[idx] >= args.threshold:
                    bell_hits.append((label, scores[idx], idx))

            now = time.monotonic()
            if bell_hits:
                # Doorbell / Bell detected!
                bell_hits.sort(key=lambda x: x[1], reverse=True)
                best_label, best_score, best_idx = bell_hits[0]

                print(
                    f"\r{Fore.YELLOW}🔔 [DOORBELL DETECTED] {best_label} "
                    f"Confidence: {best_score:.2f} {Style.RESET_ALL}"
                )

                if now - last_trigger_time >= 1.0:
                    last_trigger_time = now
                    # Normalize event name string for Pi
                    event_name = best_label.split()[0].lower()
                    print(f"  └─ 🚀 Pushing RED ALERT to Raspberry Pi: '{event_name}' ({best_score:.2f})")
                    send_event_to_pi(args.pi_host, args.port, event_name, best_score)
            else:
                # Real-time top sound output display
                top_label = ALL_CLASS_NAMES.get(top_class, f"AudioSet #{top_class}")
                if top_score >= 0.15:
                    print(f"\r[YAMNet Live] Top sound: {top_label} ({top_score:.2f})      ", end="", flush=True)

    except KeyboardInterrupt:
        print("\nStopping YAMNet Classifier...")
        stream.stop()
        stream.close()
        print("YAMNet Classifier Stopped.")

if __name__ == "__main__":
    main()
