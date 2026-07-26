#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC Mobile Audio & Control Bridge for Pokonyan (Dual Port / Dedicated Display Mode).
1. 埠號 5000: 超簡潔麥克風授權頁面（僅保留麥克風開啟按鈕與連線狀態，0 雜訊）。
2. 埠號 5001 / /display: 專門為 iPhone 17 Pro Max 小車車載螢幕量身打造的超酷炫發光控制台（大字體完整句子 + 霓虹警報門鈴卡片）。
"""

import os
import sys
import time
import ssl
import json
import socket
import base64
import struct
import hashlib
import datetime
import argparse
import threading
import urllib.request
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

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

latest_audio_status = {
    "category": "NORMAL",
    "alarm_event": "",
    "alarm_score": 0.0,
    "event": "-",
    "event_score": 0.0,
    "text": "-",
    "live_text": "-",
    "last_event_time": 0.0
}

def is_phone_connected():
    return phone_connected_event.is_set()

def wait_for_phone_connection(timeout=None):
    return phone_connected_event.wait(timeout=timeout)

# 1. 超簡潔麥克風授權頁面 (埠號 5000)
MIC_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🎙️ Pokonyan iPhone 麥克風串流</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
body { background: #0d1117; color: #c9d1d9; padding: 24px 16px; min-height: 100vh; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 16px; padding: 28px 20px; width: 100%; max-width: 400px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
h1 { font-size: 20px; color: #58a6ff; margin-bottom: 20px; }
.btn { background: #238636; color: white; border: none; border-radius: 12px; padding: 18px; font-size: 18px; font-weight: bold; width: 100%; cursor: pointer; transition: all 0.2s ease; }
.btn:active { transform: scale(0.98); }
.btn-mic-on { background: #da3633; animation: pulse 1.0s infinite alternate; }
#status-box { font-size: 14px; color: #8b949e; margin-top: 16px; line-height: 1.5; }
.link-box { margin-top: 24px; font-size: 13px; }
.link-box a { color: #58a6ff; text-decoration: none; font-weight: bold; }
@keyframes pulse { from { opacity: 0.85; } to { opacity: 1.0; } }
</style>
</head>
<body>

<div class="card">
  <h1>📱 iPhone 麥克風串流端</h1>
  <button id="btn-mic" class="btn" onclick="toggleMicrophone()">🎙️ 開啟 iPhone 麥克風 (Stream Mic to PC)</button>
  <div id="status-box">點擊按鈕授權麥克風，即可將 iPhone 當作電腦與小車麥克風使用</div>

  <div class="link-box">
    🖥️ 另一支手機/車載螢幕展示頁面：<br>
    <a href="/display" target="_blank">👉 點此開啟車載螢幕展示頁面 (/display)</a>
  </div>
</div>

<script>
let isMicStreaming = false;
let audioContext = null;
let scriptNode = null;
let mediaStream = null;
let ws = null;

async function toggleMicrophone() {
  const btn = document.getElementById('btn-mic');
  const status = document.getElementById('status-box');

  if (isMicStreaming) {
    if (ws) ws.close();
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

      const wsProtocol = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
      ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws_audio`);
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        status.textContent = '🟢 WebSocket 麥克風已連接，音訊實時傳輸中...';
        status.style.color = '#3fb950';
      };

      const source = audioContext.createMediaStreamSource(mediaStream);
      scriptNode = audioContext.createScriptProcessor(2048, 1, 1);
      
      const silenceGain = audioContext.createGain();
      silenceGain.gain.value = 0;

      scriptNode.onaudioprocess = (e) => {
        if (!isMicStreaming) return;
        const inputData = e.inputBuffer.getChannelData(0);
        const pcm16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          let s = Math.max(-1, Math.min(1, inputData[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(pcm16.buffer);
        } else {
          fetch('/stream_pcm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: pcm16.buffer
          }).catch(err => console.log('PCM upload error:', err));
        }
      };

      source.connect(scriptNode);
      scriptNode.connect(silenceGain);
      silenceGain.connect(audioContext.destination);

      isMicStreaming = true;
      btn.textContent = '🛑 iPhone 麥克風收音中 (點擊停止)';
      btn.classList.add('btn-mic-on');
    } catch (err) {
      alert('無法存取麥克風：' + err.message);
      status.textContent = '存取麥克風失敗：' + err.message;
      status.style.color = '#f85149';
    }
  }
}
</script>
</body>
</html>
"""

# 2. 專門為 iPhone 小車車載螢幕量身打造的展示頁面 (埠號 5001 或 /display)
DISPLAY_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🤖 Pokonyan Robot Display Screen</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
html, body { height: 100%; overflow: hidden; background: #030508; color: #f0f6fc; }
body { padding: 16px; display: flex; flex-direction: column; justify-content: space-between; }

/* 頂部車載 Title Bar */
.top-bar { text-align: center; background: #0d1117; padding: 12px; border-radius: 12px; border: 1px solid #21262d; margin-bottom: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }
.logo-title { font-size: 18px; font-weight: 900; color: #58a6ff; letter-spacing: 1px; display: flex; justify-content: center; align-items: center; gap: 8px; }

/* 全螢幕動態發光警報/門鈴卡片 */
.status-card { background: #0d1117; border: 2px solid #21262d; border-radius: 18px; padding: 22px 16px; text-align: center; margin-bottom: 14px; transition: all 0.3s ease; }
.banner-normal { background: rgba(35, 134, 54, 0.12); border-color: #238636; color: #3fb950; }
.banner-doorbell { background: rgba(217, 119, 6, 0.25); border-color: #f59e0b; color: #fbbf24; animation: glow-gold 1.0s infinite alternate; }
.banner-alarm { background: rgba(218, 54, 51, 0.25); border-color: #f85149; color: #ff7b72; animation: glow-red 0.8s infinite alternate; }

.status-title { font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: #8b949e; margin-bottom: 8px; font-weight: bold; }
.status-headline { font-size: 28px; font-weight: 900; letter-spacing: 0.5px; }

/* 車載超大字體高可讀性語音識別卡片 */
.stt-card { background: #0d1117; border: 1px solid #30363d; border-radius: 18px; padding: 20px 16px; flex-grow: 1; display: flex; flex-direction: column; justify-content: center; box-shadow: 0 4px 16px rgba(0,0,0,0.4); }
.stt-label { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 10px; font-weight: bold; display: flex; align-items: center; gap: 6px; }
.stt-final { font-size: 30px; font-weight: 900; color: #58a6ff; line-height: 1.35; min-height: 80px; word-break: break-word; text-shadow: 0 0 12px rgba(88, 166, 255, 0.3); }
.stt-live { font-size: 22px; font-weight: 700; color: #e3b341; line-height: 1.35; margin-top: 16px; min-height: 36px; word-break: break-word; text-shadow: 0 0 10px rgba(227, 179, 65, 0.3); }

@keyframes glow-gold { from { box-shadow: 0 0 10px rgba(245, 158, 11, 0.3); } to { box-shadow: 0 0 30px rgba(245, 158, 11, 0.9); } }
@keyframes glow-red { from { box-shadow: 0 0 10px rgba(248, 81, 73, 0.4); } to { box-shadow: 0 0 35px rgba(248, 81, 73, 1.0); } }
</style>
</head>
<body>

<!-- 車載 Header Bar -->
<div class="top-bar">
  <div class="logo-title">🤖 POKONYAN ROBOT DISPLAY</div>
</div>

<!-- 🚨 車載警報與門鈴動態標誌位 (全屏霓虹發光) -->
<div id="status-card" class="status-card banner-normal">
  <div class="status-title">ENVIRONMENT SOUND STATUS</div>
  <div id="status-headline" class="status-headline">🟢 NORMAL</div>
  <div id="sound-detail" style="font-size: 15px; margin-top: 8px; opacity: 0.85;">-</div>
</div>

<!-- 💬 車載大字體語音識別 (Final Sentence 30px + Realtime Draft 22px) -->
<div class="stt-card">
  <div class="stt-label">💬 COMPLETED SENTENCE (完整語音)</div>
  <div id="transcript" class="stt-final">-</div>
  
  <div class="stt-label" style="margin-top: 20px;">⚡ REALTIME STREAMING DRAFT (實時草稿)</div>
  <div id="live_transcript" class="stt-live">-</div>
</div>

<script>
async function pollStatus() {
  try {
    const res = await fetch('/status');
    if (!res.ok) return;
    const s = await res.json();
    const a = s.audio || s;

    const card = document.getElementById('status-card');
    const headline = document.getElementById('status-headline');
    const detail = document.getElementById('sound-detail');

    const cat = a.category || 'NORMAL';
    const evtName = (a.alarm_event || '').toUpperCase();
    const evtScore = (a.alarm_score || 0).toFixed(2);
    const liveEvt = a.event && a.event !== '-' ? `${a.event} (${(a.event_score??0).toFixed(2)})` : '-';

    if (cat === 'ALARM') {
      card.className = 'status-card banner-alarm';
      headline.textContent = '🚨 ALARM DETECTED';
      detail.textContent = `警報類型: ${evtName} | 置信度: ${evtScore}`;
    } else if (cat === 'DOORBELL') {
      card.className = 'status-card banner-doorbell';
      headline.textContent = '🔔 DOORBELL DETECTED';
      detail.textContent = `門鈴類型: ${evtName} | 置信度: ${evtScore}`;
    } else {
      card.className = 'status-card banner-normal';
      headline.textContent = '🟢 NORMAL';
      detail.textContent = `實時環境聲音: ${liveEvt}`;
    }

    document.getElementById('transcript').textContent = a.text || '-';
    document.getElementById('live_transcript').textContent = a.live_text || '-';
  } catch (e) {
    console.log("Poll error:", e);
  }
}

setInterval(pollStatus, 200);
</script>
</body>
</html>
"""

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass

def process_audio_payload(pcm_bytes):
    if not pcm_bytes:
        return

    if not phone_connected_event.is_set():
        phone_connected_event.set()
        print("\n[MobileBridge] 🎉 ✅ iPhone Microphone Connected & Authorized over WebSocket!")
        print("[MobileBridge] 🚀 Starting YAMNet Sound Classifier & CUDA RealtimeSTT Pipelines...\n")

    pcm16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    float32_samples = pcm16.astype(np.float32) / 32768.0
    audio_pcm_queue.append(float32_samples)
    stt_bytes_queue.append(pcm_bytes)

def read_ws_frame(rfile):
    head = rfile.read(2)
    if not head or len(head) < 2:
        return None, None
    b1, b2 = head[0], head[1]
    opcode = b1 & 0x0F
    is_masked = (b2 & 0x80) != 0
    payload_len = b2 & 0x7F

    if payload_len == 126:
        payload_len_bytes = rfile.read(2)
        if len(payload_len_bytes) < 2:
            return None, None
        payload_len = struct.unpack(">H", payload_len_bytes)[0]
    elif payload_len == 127:
        payload_len_bytes = rfile.read(8)
        if len(payload_len_bytes) < 8:
            return None, None
        payload_len = struct.unpack(">Q", payload_len_bytes)[0]

    masks = rfile.read(4) if is_masked else None
    data = rfile.read(payload_len)

    if is_masked and masks and len(masks) == 4 and len(data) == payload_len:
        data = bytearray(data)
        for i in range(len(data)):
            data[i] ^= masks[i % 4]
        data = bytes(data)

    return opcode, data

class MobileBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def send_html(self, html_content):
        body = html_content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def send_json(self, data_dict):
        body = json.dumps(data_dict).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        # 1. 專門車載展示頁面 (/display)
        if self.path == "/display":
            self.send_html(DISPLAY_HTML)
            return

        # 2. 超簡潔麥克風串流頁面 (/)
        if self.path == "/" or self.path.startswith("/mic"):
            self.send_html(MIC_HTML)
            return

        # 3. WebSocket Upgrade
        if self.path == "/ws_audio" or self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key")
            if key:
                GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                digest = hashlib.sha1((key + GUID).encode('utf-8')).digest()
                accept_key = base64.b64encode(digest).decode('utf-8')

                self.send_response(101, "Switching Protocols")
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept_key)
                self.end_headers()

                try:
                    while True:
                        opcode, frame_data = read_ws_frame(self.rfile)
                        if opcode is None or opcode == 8:
                            break
                        if frame_data:
                            process_audio_payload(frame_data)
                except Exception:
                    pass
                return

        # 4. Windows Local Status JSON (100% Instant, 0ms Latency)
        if self.path == "/status":
            self.send_json({
                "audio": latest_audio_status,
                "robot": {"mode": "AUTO"}
            })
            return

        self.send_error(404)

    def do_POST(self):
        if self.path == "/transcribe_text":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                text = data.get("text", "").strip()
                is_live = data.get("live", False)
                if text:
                    if is_live:
                        latest_audio_status["live_text"] = text
                    else:
                        latest_audio_status["text"] = text
                        latest_audio_status["live_text"] = ""
            except Exception:
                pass

            self.send_json({"ok": True})
            return

        if self.path == "/trigger_audio_event":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                event = data.get("event", "").strip()
                score = float(data.get("score", 0.0))
                now = time.monotonic()
                if event:
                    if event in list(ALARM_CLASSES.values()):
                        latest_audio_status["category"] = "ALARM"
                        latest_audio_status["alarm_event"] = event
                        latest_audio_status["alarm_score"] = score
                    elif event in list(DOORBELL_CLASSES.values()):
                        latest_audio_status["category"] = "DOORBELL"
                        latest_audio_status["alarm_event"] = event
                        latest_audio_status["alarm_score"] = score
                    latest_audio_status["event"] = event
                    latest_audio_status["event_score"] = score
                    latest_audio_status["last_event_time"] = now
            except Exception:
                pass

            self.send_json({"ok": True})
            return

        if self.path == "/stream_pcm":
            content_length = int(self.headers.get("Content-Length", 0))
            pcm_bytes = self.rfile.read(content_length)
            if pcm_bytes:
                process_audio_payload(pcm_bytes)
            
            self.send_json({"ok": True})
            return

        self.send_error(404)

# 專門展示頁面 Handler (埠號 5001)
class DisplayPageHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/status":
            combined_status = {
                "audio": latest_audio_status,
                "robot": {"mode": "AUTO"}
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(combined_status).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DISPLAY_HTML.encode("utf-8"))

def run_mobile_stt(pi_host, pi_port):
    print(f"[Mobile Mic STT] ⏳ Waiting for iPhone microphone connection before launching STT...")
    phone_connected_event.wait()
    print(f"[Mobile Mic STT] 🎉 iPhone connected! Initializing RealtimeSTT CUDA GPU models (tiny.en + small.en)...")
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
                latest_audio_status["live_text"] = text
                print(f"\r💬 [Mobile STT Live Draft] {text}                       ", end="", flush=True)
                send_transcript_to_pi(pi_host, pi_port, text, is_live=True)

        def process_text(text):
            nonlocal sentence_count, last_live_text
            text = text.strip()
            if not text:
                return
            sentence_count += 1
            last_live_text = ""
            latest_audio_status["text"] = text
            latest_audio_status["live_text"] = ""
            print(f"\n✅ [Mobile STT Final #{sentence_count}] {text}")
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
            'use_microphone': False,
            'post_speech_silence_duration': 0.6,
            'min_length_of_recording': 0.5,
            'spinner': False,
        }
        recorder = AudioToTextRecorder(**recorder_config)
        print(f"[Mobile Mic STT] CUDA GPU STT ready & listening on iPhone WebSocket audio feed!")

        def feed_loop():
            while True:
                time.sleep(0.02)
                while stt_bytes_queue:
                    raw_bytes = stt_bytes_queue.pop(0)
                    recorder.feed_audio(raw_bytes)

        t_feed = threading.Thread(target=feed_loop, daemon=True)
        t_feed.start()

        while True:
            recorder.text(process_text)

    except Exception as e:
        import traceback
        print(f"[Mobile Mic STT Error] {e}")
        traceback.print_exc()

def run_mobile_yamnet(pi_host, pi_port, model_path, threshold):
    print(f"[Mobile Mic YAMNet] ⏳ Waiting for iPhone microphone connection before launching YAMNet...")
    phone_connected_event.wait()
    print(f"[Mobile Mic YAMNet] 🎉 iPhone connected! Initializing YAMNet TFLite interpreter...")
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

        doorbell_scores = [(label, scores[idx]) for idx, label in DOORBELL_CLASSES.items() if idx < len(scores)]
        doorbell_sum = sum(s for _, s in doorbell_scores if s >= 0.02)
        top_doorbell = max(doorbell_scores, key=lambda x: x[1]) if doorbell_scores else ("doorbell", 0.0)

        alarm_scores = [(label, scores[idx]) for idx, label in ALARM_CLASSES.items() if idx < len(scores)]
        alarm_sum = sum(s for _, s in alarm_scores if s >= 0.02)
        top_alarm = max(alarm_scores, key=lambda x: x[1]) if alarm_scores else ("alarm", 0.0)

        now = time.monotonic()
        top_name = ALL_CLASS_NAMES.get(top_class, f"class_{top_class}")

        if alarm_sum >= 0.15 and alarm_sum >= doorbell_sum:
            name = top_alarm[0]
            score = alarm_sum
            latest_audio_status["category"] = "ALARM"
            latest_audio_status["alarm_event"] = name
            latest_audio_status["alarm_score"] = float(score)
            latest_audio_status["event"] = name
            latest_audio_status["event_score"] = float(score)
            latest_audio_status["last_event_time"] = now
            print(f"\r🚨 [Mobile Mic -> YAMNet ALARM] {name} (Sum: {score:.2f})       ", end="", flush=True)
            if now - last_trigger_time >= 0.4:
                last_trigger_time = now
                send_event_to_pi(name, score)

        elif doorbell_sum >= 0.15:
            name = top_doorbell[0]
            score = doorbell_sum
            latest_audio_status["category"] = "DOORBELL"
            latest_audio_status["alarm_event"] = name
            latest_audio_status["alarm_score"] = float(score)
            latest_audio_status["event"] = name
            latest_audio_status["event_score"] = float(score)
            latest_audio_status["last_event_time"] = now
            print(f"\r🔔 [Mobile Mic -> YAMNet DOORBELL] {name} (Sum: {score:.2f})       ", end="", flush=True)
            if now - last_trigger_time >= 0.4:
                last_trigger_time = now
                send_event_to_pi(name, score)

        else:
            if top_score >= 0.12:
                latest_audio_status["event"] = top_name
                latest_audio_status["event_score"] = float(top_score)
                print(f"\r🎵 [Mobile Mic -> YAMNet Live] {top_name} ({top_score:.2f})       ", end="", flush=True)
                if now - last_trigger_time >= 0.4:
                    last_trigger_time = now
                    send_event_to_pi(top_name, top_score)

        # Alarm / Doorbell category stays active until cleared by mission completion

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

def start_display_server_5001(ssl_context=None):
    try:
        server_5001 = ThreadedHTTPServer(("0.0.0.0", 5001), DisplayPageHandler)
        if ssl_context:
            server_5001.socket = ssl_context.wrap_socket(server_5001.socket, server_side=True)
        print(f"[Display Server 5001] 📺 Dedicated Robot Display Server running on Port 5001...")
        t_5001 = threading.Thread(target=server_5001.serve_forever, daemon=True)
        t_5001.start()
    except Exception as e:
        print(f"[Display Server 5001 Warning] {e}")

def main():
    global PI_HOST, PI_PORT
    parser = argparse.ArgumentParser(description="PC Mobile Audio Bridge Server")
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

    stt_thread = threading.Thread(target=run_mobile_stt, args=(args.pi_host, args.pi_port), daemon=True)
    stt_thread.start()

    server = ThreadedHTTPServer(("0.0.0.0", args.port), MobileBridgeHandler)

    ctx = None
    protocol = "http"
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
                ctx = None

    ips = get_all_ip_addresses()

    print(f"========================================================")
    print(f" 📱 Pokonyan Mobile Mic & Robot Car Display Server      ")
    print(f"========================================================")
    print(f"📡 Target Pi: http://{args.pi_host}:{args.pi_port}/")
    print(f"🎙️ 麥克風串流端網址 (僅需授權麥克風權限):")
    for ip in ips:
        if ip.startswith("100."):
            print(f"   👉 {protocol}://{ip}:5000/   ⭐ (Tailscale 虛擬網段 IP)")
    print(f"\n📺 專門 iPhone 車載顯示屏網址 (動態警報與大字體語句):")
    for ip in ips:
        if ip.startswith("100."):
            print(f"   👉 {protocol}://{ip}:5000/display   ⭐【車載螢幕推薦】")
    print(f"========================================================\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Mobile Bridge Server...")
        server.server_close()

if __name__ == "__main__":
    main()
