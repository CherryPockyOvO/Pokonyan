#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone Phone Microphone Audio Test Script (WebSocket + HTTP Dual Mode).
手機麥克風直通測試腳本（支持 WebSocket 長連接與 HTTP 雙模式）：
1. 手機瀏覽器透過 WebSocket/HTTP 實時傳送 16kHz PCM 麥克風音訊至電腦。
2. 電腦控制台實時顯示動態 ASCII 音量計 (Volume Meter)。
3. 電腦喇叭實時直通播放手機麥克風聲音 (Live PC Speaker Playback)。
4. 自動將音訊儲存至 mobile_mic_test.wav，隨時聽取驗證。
"""

import os
import sys
import time
import ssl
import json
import wave
import socket
import base64
import struct
import hashlib
import datetime
import argparse
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Sounddevice for live PC speaker playback
try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

# Cryptography for SSL cert generation
try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

def ensure_ssl_cert(cert_file="cert.pem", key_file="key.pem"):
    if os.path.exists(cert_file) and os.path.exists(key_file):
        return cert_file, key_file

    if not HAS_CRYPTO:
        return None, None

    print("[SSL] Generating self-signed HTTPS certificate for Mobile Microphone access...")
    try:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Pokonyan Mic Test"),
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

        return cert_file, key_file
    except Exception as e:
        print(f"[SSL Error] {e}")
        return None, None

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

# Global State
audio_bytes_history = bytearray()
total_chunks_received = 0
total_bytes_received = 0
wav_filename = "mobile_mic_test.wav"

TEST_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🎙️ 手機麥克風 ➔ 電腦 直通測試</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
body { background: #0d1117; color: #c9d1d9; padding: 16px; font-size: 15px; text-align: center; }
h1 { font-size: 20px; color: #58a6ff; margin-bottom: 16px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
.btn { background: #238636; color: white; border: none; border-radius: 8px; padding: 16px; font-size: 18px; font-weight: bold; width: 100%; cursor: pointer; margin-bottom: 12px; }
.btn:active { opacity: 0.8; }
.btn-on { background: #da3633; animation: pulse 1.0s infinite alternate; }
@keyframes pulse { from { opacity: 0.8; transform: scale(0.98); } to { opacity: 1; transform: scale(1.02); } }
#status-box { font-size: 14px; color: #8b949e; margin-top: 8px; line-height: 1.5; }
#meter-bar { width: 100%; height: 24px; background: #21262d; border-radius: 12px; overflow: hidden; margin-top: 12px; border: 1px solid #30363d; }
#meter-fill { width: 0%; height: 100%; background: linear-gradient(90deg, #238636, #e3b341, #da3633); transition: width 0.1s ease; }
</style>
</head>
<body>

<h1>🎙️ 手機麥克風 ➔ 電腦 直通測試</h1>

<div class="card">
  <button id="btn-mic" class="btn" onclick="toggleMicrophone()">🎙️ 開始測試手機麥克風 (Stream to PC)</button>
  <div id="status-box">點擊綠色按鈕授權麥克風，即可在電腦端實時聽到手機聲音與音量動態</div>
  
  <div id="meter-bar">
    <div id="meter-fill"></div>
  </div>
</div>

<script>
let isStreaming = false;
let audioContext = null;
let scriptNode = null;
let mediaStream = null;
let ws = null;

async function toggleMicrophone() {
  const btn = document.getElementById('btn-mic');
  const status = document.getElementById('status-box');
  const meterFill = document.getElementById('meter-fill');

  if (isStreaming) {
    if (ws) ws.close();
    if (scriptNode) scriptNode.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
    if (audioContext) audioContext.close();
    isStreaming = false;
    btn.textContent = '🎙️ 開始測試手機麥克風 (Stream to PC)';
    btn.classList.remove('btn-on');
    status.textContent = '麥克風測試已停止';
    status.style.color = '#8b949e';
    meterFill.style.width = '0%';
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

      // Establish WebSocket
      const wsProtocol = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
      ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws_audio`);
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        status.textContent = '🟢 WebSocket 長連接建立成功！正在實時傳送麥克風音訊...';
        status.style.color = '#7ee787';
      };

      ws.onerror = (err) => {
        console.log('WS error, using HTTP POST fallback');
      };

      const source = audioContext.createMediaStreamSource(mediaStream);
      scriptNode = audioContext.createScriptProcessor(2048, 1, 1);
      
      const silenceGain = audioContext.createGain();
      silenceGain.gain.value = 0; // Mute local speaker feedback

      scriptNode.onaudioprocess = (e) => {
        if (!isStreaming) return;
        const inputData = e.inputBuffer.getChannelData(0);
        
        let sum = 0;
        const pcm16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          let s = Math.max(-1, Math.min(1, inputData[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          sum += s * s;
        }
        let rms = Math.sqrt(sum / inputData.length);
        let volPct = Math.min(100, Math.round(rms * 400));
        meterFill.style.width = volPct + '%';

        // Send binary data over WebSocket if open, else HTTP POST
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(pcm16.buffer);
        } else {
          fetch('/stream_pcm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: pcm16.buffer
          }).catch(err => console.log('Upload error:', err));
        }
      };

      source.connect(scriptNode);
      scriptNode.connect(silenceGain);
      silenceGain.connect(audioContext.destination);

      isStreaming = true;
      btn.textContent = '🛑 正在向電腦傳送手機音訊 (點擊停止)';
      btn.classList.add('btn-on');
    } catch (err) {
      alert('無法存取麥克風：' + err.message);
      status.textContent = '存取麥克風失敗：' + err.message;
      status.style.color = '#da3633';
    }
  }
}
</script>
</body>
</html>
"""

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass

def process_audio_chunk(pcm_bytes):
    global total_chunks_received, total_bytes_received, audio_bytes_history

    if not pcm_bytes:
        return

    total_chunks_received += 1
    total_bytes_received += len(pcm_bytes)
    audio_bytes_history.extend(pcm_bytes)

    pcm16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    samples_f32 = pcm16.astype(np.float32) / 32768.0

    rms = np.sqrt(np.mean(samples_f32 ** 2))
    level_bars = int(rms * 40)
    bar_str = "█" * min(20, level_bars) + "░" * (20 - min(20, level_bars))

    if HAS_SOUNDDEVICE:
        try:
            sd.play(pcm16, samplerate=16000)
        except Exception:
            pass

    print(f"\r🔊 [手機麥克風音訊接收中] 音量計: [{bar_str}] RMS: {rms:.4f} | 封包 #{total_chunks_received} ({len(pcm_bytes)} Bytes) ", end="", flush=True)

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

class MicTestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(TEST_HTML.encode("utf-8"))
            return

        # WebSocket Upgrade
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

                print(f"\n[WebSocket] 🎉 ✅ 手機 WebSocket 長連接成功握手建立！實時收聽中...")

                # Loop reading binary WebSocket frames
                try:
                    while True:
                        opcode, frame_data = read_ws_frame(self.rfile)
                        if opcode is None or opcode == 8:  # Close frame
                            break
                        if frame_data:
                            process_audio_chunk(frame_data)
                except Exception:
                    pass
                return

        self.send_error(404)

    def do_POST(self):
        if self.path == "/stream_pcm":
            content_length = int(self.headers.get("Content-Length", 0))
            pcm_bytes = self.rfile.read(content_length)
            if pcm_bytes:
                process_audio_chunk(pcm_bytes)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        self.send_error(404)

def save_recorded_wav():
    if total_bytes_received > 0:
        print(f"\n\n💾 正在將收集到的手機麥克風音訊儲存至 {wav_filename} ({total_bytes_received} Bytes)...")
        with wave.open(wav_filename, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_bytes_history)
        print(f"✅ 錄音檔案已成功儲存為 {os.path.abspath(wav_filename)}！您可以隨時播放收聽。\n")

def main():
    parser = argparse.ArgumentParser(description="Standalone Phone Microphone Audio Tester")
    parser.add_argument("--port", type=int, default=5000, help="Web server port (default: 5000)")
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS SSL and run on pure HTTP")
    args = parser.parse_args()

    server = ThreadedHTTPServer(("0.0.0.0", args.port), MicTestHandler)

    protocol = "http"
    if not args.no_ssl:
        cert_file, key_file = ensure_ssl_cert()
        if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
                server.socket = ctx.wrap_socket(server.socket, server_side=True)
                protocol = "https"
            except Exception as e:
                print(f"[SSL Warning] TLS setup failed: {e}, falling back to HTTP")
                protocol = "http"

    ips = get_all_ip_addresses()

    print(f"========================================================")
    print(f" 🎙️ 手機麥克風電腦直通測試器 (WebSocket + HTTP 雙模版)    ")
    print(f"========================================================")
    print(f"📡 喇叭直通播放: {'已啟用 (電腦喇叭將實時播放手機聲音)' if HAS_SOUNDDEVICE else '未啟用'}")
    print(f"🌐 手機 Safari / 瀏覽器開啟測試網址:")
    for ip in ips:
        if ip.startswith("100."):
            print(f"   👉 {protocol}://{ip}:{args.port}/   ⭐【首選推薦】(Tailscale 虛擬網段 IP)")
        elif ip.startswith("10.") or ip.startswith("192.168."):
            print(f"   👉 {protocol}://{ip}:{args.port}/   🏠 (局域網實體 Wi-Fi IP)")
        else:
            print(f"   👉 {protocol}://{ip}:{args.port}/")
    print(f"💡 在 iPhone 17 Pro Max Safari 開啟網址，點擊「開始測試」對手機說話！\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n停止測試服務...")
        server.server_close()
        save_recorded_wav()

if __name__ == "__main__":
    main()
