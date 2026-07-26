#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC Mobile Audio & Control Bridge for Pokonyan.
手機 Web 端音訊與控制橋接器（全同源代理，相容 iPhone / 華為 / Android）：
1. 透過 HTTP 提供純淨、超低延遲的雙向串流。
2. 同源反向代理樹莓派的 /video_feed (視訊流), /status (狀態), /control (遙控按鈕), /mode (模式切換)。
3. 手機開啟瀏覽器即可將「手機麥克風」實時串流給電腦 YAMNet 與 RealtimeSTT 進行識別。
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
stt_bytes_queue = []
phone_connected_event = threading.Event()
PI_HOST = "100.80.242.72"
PI_PORT = 8080

def is_phone_connected():
    return phone_connected_event.is_set()

def wait_for_phone_connection(timeout=None):
    return phone_connected_event.wait(timeout=timeout)

MOBILE_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>📱 Pokonyan iPhone / 手機麥克風 & 終端控制台</title>
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
  <h1>📱 iPhone / 手機麥克風 & Pokonyan 控制台</h1>
</div>

<!-- 🎙️ 手機麥克風開關 -->
<div class="card">
  <button id="btn-mic" class="btn" onclick="toggleMicrophone()">🎙️ 開啟 iPhone 麥克風 (Stream Mic to PC)</button>
  <div id="mic-status" style="font-size: 12px; color: #8b949e; margin-top: 6px; text-align: center;">點擊按鈕授權麥克風，即可將 iPhone 當作電腦麥克風使用</div>
</div>

<!-- 🚨 聲音識別與門鈴標誌位 -->
<div class="card">
  <div class="label">Doorbell / Alarm Status (Flag 標誌位)</div>
  <div id="alarm_flag_box" class="value"><span style="color:#7ee787;">🟢 NORMAL (No Event)</span></div>
  
  <div class="label" style="margin-top: 8px;">Realtime Sound Classification (實時聲音分類)</div>
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

async function toggleMicrophone() {
  const btn = document.getElementById('btn-mic');
  const status = document.getElementById('mic-status');

  if (isMicStreaming) {
    if (scriptNode) scriptNode.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
    if (audioContext) audioContext.close();
    isMicStreaming = false;
    btn.textContent = '🎙️ 開啟 iPhone 麥克風 (Stream Mic to PC)';
    btn.classList.remove('btn-mic-on');
    status.textContent = '麥克風已停止串流';
    status.style.color = '#8b949e';
  } else {
    try {
      const getMedia = (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) ? 
        (args) => navigator.mediaDevices.getUserMedia(args) : 
        (args) => new Promise((res, rej) => (navigator.getUserMedia || navigator.webkitGetUserMedia || navigator.mozGetUserMedia).call(navigator, args, res, rej));

      mediaStream = await getMedia({ audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }, video: false });
      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      if (audioContext.state === 'suspended') {
        await audioContext.resume();
      }
      const source = audioContext.createMediaStreamSource(mediaStream);
      
      scriptNode = audioContext.createScriptProcessor(4096, 1, 1);
      
      scriptNode.onaudioprocess = (e) => {
        if (!isMicStreaming) return;
        const inputData = e.inputBuffer.getChannelData(0);
        const pcm16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          let s = Math.max(-1, Math.min(1, inputData[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
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
      alert('無法存取 iPhone 麥克風：' + err.message);
      status.textContent = '存取麥克風失敗：' + err.message;
      status.style.color = '#da3633';
    }
  }
}

async function setMode(mode) {
  fetch('/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: mode })
  });
}

async function sendCmd(cmd) {
  fetch('/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: cmd })
  });
}

async function pollStatus() {
  try {
    const res = await fetch('/status');
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

    def handle_error(self, request, client_address):
        pass

class MobileBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/mobile"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(MOBILE_HTML.encode("utf-8"))
            return

        # Reverse Proxy: Robot Status JSON
        if self.path == "/status":
            url = f"http://{PI_HOST}:{PI_PORT}/status"
            try:
                with urllib.request.urlopen(url, timeout=2.0) as resp:
                    data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(data)
            except Exception as e:
                self.send_error(502, f"Proxy Status Error: {e}")
            return

        self.send_error(404)

    def do_POST(self):
        # Audio stream from mobile microphone
        if self.path == "/stream_pcm":
            content_length = int(self.headers.get("Content-Length", 0))
            pcm_bytes = self.rfile.read(content_length)
            if pcm_bytes:
                if not phone_connected_event.is_set():
                    phone_connected_event.set()
                    print("\n[MobileBridge] 🎉 ✅ iPhone Microphone Connected & Authorized!")
                    print("[MobileBridge] 🚀 Starting YAMNet Sound Classifier & CUDA RealtimeSTT Pipelines...\n")

                pcm16 = np.frombuffer(pcm_bytes, dtype=np.int16)
                float32_samples = pcm16.astype(np.float32) / 32768.0
                audio_pcm_queue.append(float32_samples)
                stt_bytes_queue.append(pcm_bytes)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        # Reverse Proxy: WASD Control Command
        if self.path == "/control":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            url = f"http://{PI_HOST}:{PI_PORT}/control"
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(data)
            except Exception as e:
                self.send_error(502, f"Proxy Control Error: {e}")
            return

        # Reverse Proxy: Mode Switch Command
        if self.path == "/mode":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            url = f"http://{PI_HOST}:{PI_PORT}/mode"
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(data)
            except Exception as e:
                self.send_error(502, f"Proxy Mode Error: {e}")
            return

        self.send_error(404)

def run_mobile_stt(pi_host, pi_port):
    phone_connected_event.wait()
    print(f"[Mobile Mic STT] Initializing RealtimeSTT CUDA GPU models (tiny.en + small.en)...")
    try:
        from pc_stt_client import send_transcript_to_pi
        from RealtimeSTT import AudioToTextRecorder

        sentence_count = 0
        last_live_text = ""

        def text_detected(text):
            nonlocal last_live_text
            text = text.strip()
            if text and text != last_live_text:
                last_live_text = text
                send_transcript_to_pi(pi_host, pi_port, text, is_live=True)

        def process_text(text):
            nonlocal sentence_count, last_live_text
            text = text.strip()
            if not text:
                return
            sentence_count += 1
            last_live_text = ""
            print(f"[Mobile STT Final #{sentence_count}] {text}")
            send_transcript_to_pi(pi_host, pi_port, text, is_live=False)

        recorder_config = {
            'model': 'small.en',
            'realtime_model_type': 'tiny.en',
            'language': 'en',
            'device': 'cuda',
            'compute_type': 'float16',
            'enable_realtime_transcription': True,
            'realtime_processing_pause': 0.15,
            'on_realtime_transcription_update': text_detected,
            'post_speech_silence_duration': 0.6,
            'min_length_of_recording': 0.5,
            'spinner': False,
        }
        recorder = AudioToTextRecorder(**recorder_config)
        print(f"[Mobile Mic STT] CUDA GPU STT listening on iPhone audio feed!")

        while True:
            time.sleep(0.02)
            while stt_bytes_queue:
                raw_bytes = stt_bytes_queue.pop(0)
                recorder.feed_audio(raw_bytes)
    except Exception as e:
        print(f"[Mobile Mic STT Warning] {e}")

def run_mobile_yamnet(pi_host, pi_port, model_path, threshold):
    phone_connected_event.wait()
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
        while audio_pcm_queue:
            chunk = audio_pcm_queue.pop(0)
            if len(chunk) >= window_samples:
                audio_buffer[:] = chunk[-window_samples:]
            else:
                audio_buffer = np.roll(audio_buffer, -len(chunk))
                audio_buffer[-len(chunk):] = chunk

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

import socket
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
    global PI_HOST, PI_PORT
    parser = argparse.ArgumentParser(description="PC Mobile Audio & Proxy Bridge Server")
    parser.add_argument("--port", type=int, default=5000, help="PC Mobile Web server port (default: 5000)")
    parser.add_argument("--pi-host", default="100.80.242.72", help="Raspberry Pi IP (default: 100.80.242.72)")
    parser.add_argument("--pi-port", type=int, default=8080, help="Raspberry Pi web port (default: 8080)")
    parser.add_argument("--model", default="model/yamnet.tflite", help="Path to yamnet.tflite model")
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS SSL and run on pure HTTP")
    args = parser.parse_args()

    PI_HOST = args.pi_host
    PI_PORT = args.pi_port

    model_path = os.path.abspath(args.model)
    if not os.path.exists(model_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(base_dir, "model", "yamnet.tflite")

    yamnet_thread = threading.Thread(target=run_mobile_yamnet, args=(args.pi_host, args.pi_port, model_path, 0.20), daemon=True)
    yamnet_thread.start()

    server = ThreadedHTTPServer(("0.0.0.0", args.port), MobileBridgeHandler)
    
    if not args.no_ssl:
        cert_file, key_file = ensure_ssl_cert()
        if os.path.exists(cert_file) and os.path.exists(key_file):
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
                server.socket = ctx.wrap_socket(server.socket, server_side=True)
                protocol = "https"
            except Exception as e:
                print(f"[SSL Warning] TLS init failed: {e}, running pure HTTP")
                protocol = "http"
        else:
            protocol = "http"
    else:
        protocol = "http"

    ips = get_all_ip_addresses()

    print(f"========================================================")
    print(f" 📱 Pokonyan Mobile Mic & Reverse Proxy Bridge Server  ")
    print(f"========================================================")
    print(f"📡 Target Pi: http://{args.pi_host}:{args.pi_port}/")
    print(f"🌐 iPhone / Mobile HTTPS URLs (Required for iOS Safari Mic):")
    for ip in ips:
        if ip.startswith("100."):
            print(f"   👉 {protocol}://{ip}:{args.port}/   ⭐【首選推薦】(Tailscale 虛擬網段 IP)")
        elif ip.startswith("10.") or ip.startswith("192.168."):
            print(f"   👉 {protocol}://{ip}:{args.port}/   🏠 (局域網實體 Wi-Fi IP)")
        else:
            print(f"   👉 {protocol}://{ip}:{args.port}/")
    print(f"🎙️ 請在 iPhone Safari 開啟 https:// 網址，並授權麥克風！\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Mobile Bridge Server...")
        server.server_close()

if __name__ == "__main__":
    main()
