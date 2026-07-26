#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC Mobile Audio & Control Bridge for Pokonyan.
華為手機 Web 端音訊與控制橋接器：
1. 手機打開瀏覽器即可將「手機麥克風」透過 WebAudio 實時串流至電腦（無需安裝任何 App）。
2. 同時在手機螢幕上展示完整 Pokonyan 控制台（相機畫面、DOORBELL/ALARM 警報狀態、STT 文字與 WASD 觸控遙控）。
"""

import os
import sys
import time
import json
import argparse
import urllib.request
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure LiteRT/TFLite interpreter is installed for YAMNet
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
            print("[Mobile Bridge] Installing missing 'ai-edge-litert' TFLite interpreter...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "ai-edge-litert"])
            from ai_edge_litert.interpreter import Interpreter

# AudioSet class ontology mapping
DOORBELL_CLASSES = {
    349: "doorbell", 350: "ding-dong", 173: "bell", 195: "church_bell",
    196: "jingle_bell", 197: "bicycle_bell", 198: "chime", 200: "campanology",
    201: "carillon", 202: "tubular_bells", 384: "telephone_ring", 385: "ringtone"
}

ALARM_CLASSES = {
    382: "alarm", 389: "alarm_clock", 390: "siren", 391: "fire_alarm",
    393: "civil_defense_siren", 394: "buzzer", 395: "police_siren",
    396: "ambulance_siren", 397: "fire_engine_siren"
}

GENERAL_CLASSES = {
    0: "speech", 16: "laughter", 45: "cough", 48: "snore",
    51: "whistling", 57: "applause", 74: "dog_bark", 81: "cat_meow", 137: "music"
}

ALL_CLASS_NAMES = {**DOORBELL_CLASSES, **ALARM_CLASSES, **GENERAL_CLASSES}

# Global State
audio_pcm_queue = []
latest_phone_sound = "-"
latest_phone_score = 0.0

MOBILE_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>📱 Pokonyan 華為手機麥克風 & 終端控制台</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
body { background: #0d1117; color: #c9d1d9; padding: 12px; font-size: 14px; }
.header { text-align: center; margin-bottom: 12px; }
.header h1 { font-size: 18px; color: #58a6ff; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.label { font-size: 11px; color: #8b949e; text-transform: uppercase; margin-bottom: 4px; font-weight: bold; }
.value { font-size: 15px; font-weight: bold; min-height: 22px; word-break: break-word; }
.btn { background: #238636; color: white; border: none; border-radius: 6px; padding: 12px; font-size: 15px; font-weight: bold; width: 100%; cursor: pointer; text-align: center; }
.btn:active { opacity: 0.8; }
.btn-mic-on { background: #da3633; animation: pulse 1.0s infinite alternate; }
.btn-mode { background: #21262d; border: 1px solid #30363d; font-size: 13px; padding: 8px; }
.btn-mode.active { background: #1f6feb; border-color: #388bfd; }
.wasd-pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; max-width: 260px; margin: 8px auto; }
.btn-wasd { background: #21262d; color: white; font-size: 16px; padding: 14px; border: 1px solid #30363d; border-radius: 6px; }
.btn-wasd:active { background: #1f6feb; }
.btn-brake { background: #8b949e; color: #0d1117; }
.doorbell-banner { background: #d97706; color: #ffffff; padding: 4px 10px; border-radius: 4px; font-weight: bold; display: inline-block; animation: pulse 0.8s infinite alternate; }
.red-alert-banner { background: #da3633; color: #ffffff; padding: 4px 10px; border-radius: 4px; font-weight: bold; display: inline-block; animation: pulse 0.8s infinite alternate; }
@keyframes pulse { from { opacity: 0.8; transform: scale(0.98); } to { opacity: 1; transform: scale(1.02); } }
.video-container { width: 100%; border-radius: 6px; overflow: hidden; background: #000; margin-bottom: 12px; }
.video-container img { width: 100%; height: auto; display: block; }
</style>
</head>
<body>

<div class="header">
  <h1>📱 華為手機麥克風 & Pokonyan 控制台</h1>
</div>

<!-- 🎙️ 華為手機麥克風開關 -->
<div class="card">
  <button id="btn-mic" class="btn" onclick="toggleMicrophone()">🎙️ 開啟華為手機麥克風 (Stream Mic to PC)</button>
  <div id="mic-status" style="font-size: 12px; color: #8b949e; margin-top: 6px; text-align: center;">點擊按鈕授權麥克風，即可將手機當作電腦麥克風使用</div>
</div>

<!-- 🎥 樹莓派即時視訊 -->
<div class="video-container">
  <img id="stream-img" src="" alt="即時視訊載入中...">
</div>

<!-- 🚨 聲音識別與門鈴標誌位 -->
<div class="card">
  <div class="label">Doorbell / Alarm Status (Flag 標誌位)</div>
  <div id="alarm_flag_box" class="value"><span style="color:#7ee787;">🟢 NORMAL (No Event)</span></div>
  
  <div class="label" style="margin-top: 8px;">Realtime Sound Classification (手機麥克風實時分類)</div>
  <div id="event" class="value" style="color:#58a6ff;">-</div>
</div>

<!-- 💬 STT 語音識別展演 -->
<div class="card">
  <div class="label">Last Completed Sentence (Final STT)</div>
  <div id="transcript" class="value" style="color:#7ee787;">-</div>
  
  <div class="label" style="margin-top: 8px;">Realtime Live Recognition (Streaming Draft)</div>
  <div id="live_transcript" class="value" style="color:#e3b341;">-</div>
</div>

<!-- 🎮 遙控觸控面板 -->
<div class="card">
  <div class="label">Control Mode</div>
  <div style="display: flex; gap: 8px; margin-bottom: 8px;">
    <button id="btn-auto" class="btn btn-mode active" onclick="setMode('AUTO')" style="flex:1;">🤖 AUTO Mode</button>
    <button id="btn-manual" class="btn btn-mode" onclick="setMode('MANUAL')" style="flex:1;">🎮 MANUAL Mode</button>
  </div>
  <div class="wasd-pad">
    <button class="btn btn-wasd" onclick="sendCmd('WA')">WA ↖</button>
    <button class="btn btn-wasd" onclick="sendCmd('W')">W ↑</button>
    <button class="btn btn-wasd" onclick="sendCmd('WD')">WD ↗</button>
    <button class="btn btn-wasd" onclick="sendCmd('A')">A ←</button>
    <button class="btn btn-wasd btn-brake" onclick="sendCmd('B')">B (Brake)</button>
    <button class="btn btn-wasd" onclick="sendCmd('D')">D →</button>
    <button class="btn btn-wasd" onclick="sendCmd('SA')">SA ↙</button>
    <button class="btn btn-wasd" onclick="sendCmd('S')">S ↓</button>
    <button class="btn btn-wasd" onclick="sendCmd('SD')">SD ↘</button>
  </div>
</div>

<script>
let isMicStreaming = false;
let audioContext = null;
let scriptNode = null;
let mediaStream = null;
let piHost = window.location.hostname;

document.getElementById('stream-img').src = `http://${piHost}:8080/video_feed`;

async function toggleMicrophone() {
  const btn = document.getElementById('btn-mic');
  const status = document.getElementById('mic-status');

  if (isMicStreaming) {
    // Stop Microphone
    if (scriptNode) scriptNode.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
    if (audioContext) audioContext.close();
    isMicStreaming = false;
    btn.textContent = '🎙️ 開啟 iPhone 麥克風 (Stream Mic to PC)';
    btn.classList.remove('btn-mic-on');
    status.textContent = '麥克風已停止串流';
    status.style.color = '#8b949e';
  } else {
    // Start Microphone
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert('iOS Safari 安全限制：請確保網址為 https:// ！');
        return;
      }
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }, video: false });
      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      if (audioContext.state === 'suspended') {
        await audioContext.resume();
      }
      const source = audioContext.createMediaStreamSource(mediaStream);
      
      // 4096 samples buffer (~0.25s at 16kHz)
      scriptNode = audioContext.createScriptProcessor(4096, 1, 1);
      
      scriptNode.onaudioprocess = (e) => {
        if (!isMicStreaming) return;
        const inputData = e.inputBuffer.getChannelData(0);
        // Convert Float32Array to Int16Array PCM
        const pcm16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          let s = Math.max(-1, Math.min(1, inputData[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        // Send PCM binary data to PC server
        fetch('/stream_pcm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: pcm16.buffer
        }).catch(err => console.log('PCM upload error:', err));
      };

      source.connect(scriptNode);
      scriptNode.connect(audioContext.destination);

      isMicStreaming = true;
      btn.textContent = '🛑 iPhone 麥克風收音中 (點擊停止)';
      btn.classList.add('btn-mic-on');
      status.textContent = '🟢 iPhone 麥克風正在向電腦實時串流音訊中...';
      status.style.color = '#7ee787';
    } catch (err) {
      alert('無法存取 iPhone 麥克風：' + err.message + '\\n\\n提示：請確保在 iPhone 網址列中使用 https:// 存取！');
      status.textContent = '存取麥克風失敗，請確保使用 HTTPS 並在 Safari 允許權限';
      status.style.color = '#da3633';
    }
  }
}

async function setMode(mode) {
  fetch(`http://${piHost}:8080/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: mode })
  });
}

async function sendCmd(cmd) {
  fetch(`http://${piHost}:8080/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: cmd })
  });
}

async function pollStatus() {
  try {
    const res = await fetch(`http://${piHost}:8080/status`);
    const s = await res.json();
    const a = s.audio || {};
    const r = s.robot || {};

    document.getElementById('btn-auto').classList.toggle('active', (r.mode === 'AUTO'));
    document.getElementById('btn-manual').classList.toggle('active', (r.mode === 'MANUAL'));

    const flagBox = document.getElementById('alarm_flag_box');
    const cat = a.category || 'NORMAL';
    const evtName = (a.alarm_event || '').toUpperCase();
    const evtScore = (a.alarm_score || 0).toFixed(2);

    if (cat === 'ALARM') {
      flagBox.innerHTML = `<span class="red-alert-banner">🚨 ALARM DETECTED (${evtName} / ${evtScore}) 🚨</span>`;
    } else if (cat === 'DOORBELL') {
      flagBox.innerHTML = `<span class="doorbell-banner">🔔 DOORBELL DETECTED (${evtName} / ${evtScore}) 🔔</span>`;
    } else {
      flagBox.innerHTML = `<span style="color:#7ee787;">🟢 NORMAL (No Event)</span>`;
    }

    const eventBox = document.getElementById('event');
    if (a.event && a.event !== '-') {
      eventBox.innerHTML = `<span style="color:#58a6ff;">🎵 ${a.event} (${(a.event_score??0).toFixed(2)})</span>`;
    } else {
      eventBox.textContent = '-';
    }

    document.getElementById('transcript').textContent = a.text || '-';
    document.getElementById('live_transcript').textContent = a.live_text || '-';
  } catch (e) {}
}

setInterval(pollStatus, 300);
</script>
</body>
</html>
"""

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class MobileBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress standard HTTP access logs for clean console

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/mobile"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(MOBILE_HTML.encode("utf-8"))
            return
        
        self.send_error(404)

    def do_POST(self):
        if self.path == "/stream_pcm":
            content_length = int(self.headers.get("Content-Length", 0))
            pcm_bytes = self.rfile.read(content_length)
            if pcm_bytes:
                # Convert Int16 PCM to float32 numpy array
                pcm16 = np.frombuffer(pcm_bytes, dtype=np.int16)
                float32_samples = pcm16.astype(np.float32) / 32768.0
                audio_pcm_queue.append(float32_samples)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        self.send_error(404)

def run_mobile_yamnet(pi_host, pi_port, model_path, threshold):
    print(f"[Mobile Mic YAMNet] Initializing YAMNet TFLite interpreter...")
    interp = Interpreter(model_path=model_path)
    input_details = interp.get_input_details()
    output_details = interp.get_output_details()

    window_samples = 15600  # 0.975s for YAMNet
    interp.resize_tensor_input(input_details[0]["index"], [window_samples])
    interp.allocate_tensors()

    audio_buffer = np.zeros(window_samples, dtype=np.float32)
    last_trigger_time = 0.0

    def send_event_to_pi(event, score):
        url = f"http://{pi_host}:{pi_port}/trigger_audio_event"
        payload = json.dumps({"event": event, "score": float(score)}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return True
        except Exception:
            return False

    while True:
        time.sleep(0.20)
        # Drain audio_pcm_queue
        while audio_pcm_queue:
            chunk = audio_pcm_queue.pop(0)
            if len(chunk) >= window_samples:
                audio_buffer[:] = chunk[-window_samples:]
            else:
                audio_buffer = np.roll(audio_buffer, -len(chunk))
                audio_buffer[-len(chunk):] = chunk

        # Run YAMNet inference
        interp.set_tensor(input_details[0]["index"], audio_buffer)
        interp.invoke()
        scores = interp.get_tensor(output_details[0]["index"])[0]

        top_indices = np.argsort(scores)[::-1][:3]
        top_class = top_indices[0]
        top_score = scores[top_class]

        alarm_hits = []
        for idx, label in ALARM_CLASSES.items():
            if idx < len(scores) and scores[idx] >= threshold:
                alarm_hits.append((label, scores[idx]))

        doorbell_hits = []
        for idx, label in DOORBELL_CLASSES.items():
            if idx < len(scores) and scores[idx] >= threshold:
                doorbell_hits.append((label, scores[idx]))

        now = time.monotonic()
        top_name = ALL_CLASS_NAMES.get(top_class, f"class_{top_class}")

        if alarm_hits:
            alarm_hits.sort(key=lambda x: x[1], reverse=True)
            name, score = alarm_hits[0]
            print(f"\r🚨 [Mobile Mic -> YAMNet ALARM] {name} ({score:.2f})       ", end="", flush=True)
            if now - last_trigger_time >= 0.4:
                last_trigger_time = now
                send_event_to_pi(name, score)

        elif doorbell_hits:
            doorbell_hits.sort(key=lambda x: x[1], reverse=True)
            name, score = doorbell_hits[0]
            print(f"\r🔔 [Mobile Mic -> YAMNet DOORBELL] {name} ({score:.2f})       ", end="", flush=True)
            if now - last_trigger_time >= 0.4:
                last_trigger_time = now
                send_event_to_pi(name, score)

        else:
            if top_score >= 0.15:
                print(f"\r🎵 [Mobile Mic -> YAMNet Live] {top_name} ({top_score:.2f})       ", end="", flush=True)
                if now - last_trigger_time >= 0.4:
                    last_trigger_time = now
                    send_event_to_pi(top_name, top_score)

import ssl
import datetime

def ensure_ssl_cert(cert_file="cert.pem", key_file="key.pem"):
    if os.path.exists(cert_file) and os.path.exists(key_file):
        return cert_file, key_file

    print("[SSL] Generating self-signed HTTPS certificate for iOS Safari / iPhone Microphone access...")
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Pokonyan Mobile Bridge"),
        ])
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.datetime.utcnow()
        ).not_valid_after(
            datetime.datetime.utcnow() + datetime.timedelta(days=365)
        ).sign(key, hashes.SHA256())

        with open(key_file, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        with open(cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        print(f"[SSL] Certificate created: {cert_file}, {key_file}")
    except Exception as e:
        print(f"[SSL Warning] Failed to generate certificate: {e}")

    return cert_file, key_file

import socket

def get_all_ip_addresses():
    ip_list = []
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ip_list.append(ip)
    except Exception:
        pass
    return ip_list

def main():
    parser = argparse.ArgumentParser(description="PC Mobile Audio Bridge Server")
    parser.add_argument("--port", type=int, default=5000, help="PC Mobile Web server port (default: 5000)")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--pi-port", type=int, default=8080, help="Raspberry Pi web port (default: 8080)")
    parser.add_argument("--model", default="model/yamnet.tflite", help="Path to yamnet.tflite model")
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS SSL and run on pure HTTP")
    args = parser.parse_args()

    model_path = os.path.abspath(args.model)
    if not os.path.exists(model_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(base_dir, "model", "yamnet.tflite")

    # Start YAMNet processing thread
    yamnet_thread = threading.Thread(target=run_mobile_yamnet, args=(args.pi_host, args.pi_port, model_path, 0.20), daemon=True)
    yamnet_thread.start()

    server = ThreadedHTTPServer(("0.0.0.0", args.port), MobileBridgeHandler)

    if not args.no_ssl:
        cert_file, key_file = ensure_ssl_cert()
        if os.path.exists(cert_file) and os.path.exists(key_file):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
            protocol = "https"
        else:
            protocol = "http"
    else:
        protocol = "http"

    ips = get_all_ip_addresses()

    print(f"========================================================")
    print(f" 📱 Pokonyan Mobile Mic & Control Bridge Server Started!")
    print(f"========================================================")
    print(f"📡 Target Pi: http://{args.pi_host}:{args.pi_port}/")
    print(f"🌐 iPhone Safari URLs (Try in order on iPhone 17 Pro Max):")
    for ip in ips:
        print(f"   👉 {protocol}://{ip}:{args.port}/")
    print(f"🎙️ Open {protocol}:// in Safari on iPhone 17 Pro Max to grant mic!\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Mobile Bridge Server...")
        server.server_close()

if __name__ == "__main__":
    main()
