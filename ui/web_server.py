# -*- coding: utf-8 -*-
"""Threaded web monitor with video stream, WASD manual control, AUTO/MANUAL mode toggle, and emergency stop."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shoe Robot Dual-Mode Control</title>
<style>
body{margin:0;background:#0d1117;color:#c9d1d9;font:15px system-ui,sans-serif;height:100vh;overflow:hidden}
main{max-width:1440px;margin:auto;padding:10px 16px;height:100vh;box-sizing:border-box;display:flex;flex-direction:column}
h1{font-size:20px;color:#58a6ff;margin:0 0 10px 0;display:flex;align-items:center;justify-content:space-between}
.dashboard{display:grid;grid-template-columns:1.2fr 1fr;gap:12px;flex:1;min-height:0}
.left-col,.right-col{display:flex;flex-direction:column;gap:10px;min-height:0}
img{width:100%;max-height:410px;object-fit:contain;background:#000;border:1px solid #30363d;border-radius:8px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px}
.label{color:#8b949e;font-size:12px}.value{font-size:17px;margin:2px 0 6px;font-weight:600}
#state{color:#7ee787}#mode-val{color:#58a6ff}#error{color:#ff7b72;white-space:pre-wrap;margin-top:6px;font-size:12px}
.mode-btn-group{display:flex;gap:8px;margin-bottom:8px}
.btn{border:0;border-radius:6px;padding:8px 14px;font-weight:bold;cursor:pointer;transition:all 0.2s}
.btn-mode{background:#21262d;color:#c9d1d9;border:1px solid #30363d;flex:1}
.btn-mode.active{background:#238636;color:white;border-color:#2ea043}
.btn-stop{background:#da3633;color:white;width:100%;padding:10px;font-size:14px;margin-top:6px}
.btn-stop:hover{background:#f85149}
.wasd-pad{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;max-width:240px;margin:6px auto}
.btn-wasd{background:#21262d;color:white;font-size:15px;padding:10px;border:1px solid #30363d;border-radius:6px}
.btn-wasd:active,.btn-wasd.active{background:#1f6feb;border-color:#388bfd}
.btn-brake{background:#8b949e;color:#0d1117}
.btn-brake:active,.btn-brake.active{background:#ff7b72;color:white}
.wasd-hint{font-size:11px;color:#8b949e;text-align:center;margin-top:4px}
.doorbell-banner{background:#d97706;color:#ffffff;padding:3px 8px;border-radius:4px;font-weight:bold;display:inline-block;animation:alert-pulse 0.8s infinite alternate}
.red-alert-banner{background:#da3633;color:#ffffff;padding:3px 8px;border-radius:4px;font-weight:bold;display:inline-block;animation:alert-pulse 0.8s infinite alternate}
@keyframes alert-pulse{from{opacity:0.8;transform:scale(0.98)}to{opacity:1;transform:scale(1.02)}}
.flex-row{display:flex;gap:12px;justify-content:space-between}
.flex-row > div{flex:1}
</style>
</head>
<body><main>
<h1>
  <span>🤖 Raspberry Pi 5 Pokonyan Control</span>
  <button class="btn btn-stop" style="width:auto;padding:6px 14px;margin:0" onclick="emergencyStop()">🚨 STOP</button>
</h1>

<div class="dashboard">
  <!-- 👈 Left Column: Camera Feed & Status -->
  <div class="left-col">
    <img src="/video_feed" alt="YOLO camera stream">
    <section class="card">
      <div class="flex-row">
        <div><div class="label">Current Status</div><div id="state" class="value">STARTING</div></div>
        <div><div class="label">Decision / Reason</div><div id="reason" class="value">-</div></div>
        <div><div class="label">Motor PWM (L/R)</div><div id="command" class="value">0 / 0</div></div>
      </div>
    </section>
  </div>

  <!-- 👉 Right Column: Mode Controls, WASD, Hardware Data & Audio Fusion -->
  <div class="right-col">
    <section class="card">
      <div class="label">Control Mode</div>
      <div class="mode-btn-group">
        <button id="btn-auto" class="btn btn-mode active" onclick="setMode('AUTO')">🤖 AUTO Mode</button>
        <button id="btn-manual" class="btn btn-mode" onclick="setMode('MANUAL')">🎮 MANUAL Mode</button>
      </div>
      <div class="wasd-pad">
        <button class="btn btn-wasd" id="key-wa" onclick="sendCmd('WA')">WA ↖</button>
        <button class="btn btn-wasd" id="key-w" onclick="sendCmd('W')">W ↑</button>
        <button class="btn btn-wasd" id="key-wd" onclick="sendCmd('WD')">WD ↗</button>
        <button class="btn btn-wasd" id="key-a" onclick="sendCmd('A')">A ←</button>
        <button class="btn btn-wasd btn-brake" id="key-b" onclick="sendCmd('B')">B (Brake)</button>
        <button class="btn btn-wasd" id="key-d" onclick="sendCmd('D')">D →</button>
        <button class="btn btn-wasd" id="key-sa" onclick="sendCmd('SA')">SA ↙</button>
        <button class="btn btn-wasd" id="key-s" onclick="sendCmd('S')">S ↓</button>
        <button class="btn btn-wasd" id="key-sd" onclick="sendCmd('SD')">SD ↘</button>
      </div>
      <div class="wasd-hint">WASD / Arrow Keys (B / Space = Brake)</div>
    </section>

    <section class="card">
      <div class="flex-row" style="margin-bottom:8px;">
        <div>
          <div class="label">💥 Bumper Collision (碰撞狀態)</div>
          <div id="bumper_status" class="value" style="font-weight:bold;">🟢 B0 (NO BUMP 沒撞擊)</div>
        </div>
        <div>
          <div class="label">🔍 Shoe Seeking (拖鞋追蹤狀態)</div>
          <div id="seeking_status" class="value" style="font-weight:bold;">💤 WANDERING (普通漫遊)</div>
        </div>
      </div>
      <div class="flex-row">
        <div><div class="label">Shoe target</div><div id="target" class="value">not seen</div></div>
        <div><div class="label">YOLO FPS</div><div id="yolo_fps" class="value">0.0 FPS</div></div>
      </div>
      <div class="flex-row">
        <div><div class="label">Ultrasonic</div><div id="distance" class="value">-</div></div>
        <div><div class="label">Arduino Serial</div><div id="arduino" class="value">-</div></div>
      </div>
    </section>

    <section class="card">
      <div class="label">Doorbell / Alarm Status (Flag 標誌位)</div>
      <div id="alarm_flag_box" class="value"><span style="color:#7ee787;font-weight:bold;">🟢 NORMAL (No Event)</span></div>
      
      <div class="label">Realtime Sound Classification (實時聲音分類)</div>
      <div id="event" class="value" style="color:#58a6ff;">-</div>
      
      <div class="label">Last Completed Sentence (Final STT)</div>
      <div id="transcript" class="value" style="color:#7ee787">-</div>
      
      <div class="label">Realtime Live Recognition (Streaming Draft)</div>
      <div id="live_transcript" class="value" style="color:#e3b341">-</div>
      <div id="error"></div>
    </section>
  </div>
</div>

<script>
const show=(id,value)=>document.getElementById(id).textContent=value;
let currentMode = "AUTO";

async function poll(){
  try{
    const s=await (await fetch('/status',{cache:'no-store'})).json();
    const r=s.robot||{},v=s.vision||{},a=s.audio||{},m=s.motor||{};
    currentMode = r.mode || "AUTO";
    
    document.getElementById('btn-auto').classList.toggle('active', currentMode === 'AUTO');
    document.getElementById('btn-manual').classList.toggle('active', currentMode === 'MANUAL');
    
    show('state', `[${currentMode}] ` + (r.state||'IDLE'));
    show('reason', r.reason||'-');
    show('command', (r.command_left??0) + ' / ' + (r.command_right??0) + ' PWM');
    
    const bumperPressed = m.bumper_pressed || false;
    const bumperBox = document.getElementById('bumper_status');
    if (bumperPressed) {
      bumperBox.innerHTML = `<span class="red-alert-banner">💥 B1 (COLLISION 撞擊)</span>`;
    } else {
      bumperBox.innerHTML = `<span style="color:#7ee787;font-weight:bold;">🟢 B0 (NO BUMP 沒撞擊)</span>`;
    }

    const autoState = r.state || 'IDLE';
    const seekingBox = document.getElementById('seeking_status');
    if (currentMode !== 'AUTO') {
      seekingBox.innerHTML = `<span style="color:#8b949e;">🎮 MANUAL MODE (手動控制)</span>`;
    } else if (autoState === 'TRACKING_SHOE') {
      seekingBox.innerHTML = `<span class="doorbell-banner">🔍 SEEKING SHOE (尋找鞋子中)</span>`;
    } else if (autoState === 'HIT_SHOE') {
      seekingBox.innerHTML = `<span class="red-alert-banner">👟 HIT SHOE (已撞到鞋子, 停留2秒)</span>`;
    } else {
      seekingBox.innerHTML = `<span style="color:#58a6ff;">💤 WANDERING (普通漫遊)</span>`;
    }
    
    const t=v.target;
    show('target', t ? 'x=' + t.centre_x.toFixed(2) + ', h=' + t.height_ratio.toFixed(2) : 'not seen');
    show('yolo_fps', (v.fps ?? 0.0).toFixed(1) + ' FPS');
    show('distance', m.distance_cm == null ? '-' : m.distance_cm.toFixed(1) + ' cm');
    show('arduino', m.ready ? 'CONNECTED' : (m.connected ? 'WAITING' : 'OFFLINE'));
    
    const flagBox = document.getElementById('alarm_flag_box');
    const cat = a.category || 'NORMAL';
    const evtName = (a.alarm_event || '').toUpperCase();
    const evtScore = (a.alarm_score || 0).toFixed(2);

    if (cat === 'ALARM') {
      flagBox.innerHTML = `<span class="red-alert-banner">🚨 ALARM DETECTED (${evtName} / ${evtScore}) 🚨</span>`;
    } else if (cat === 'DOORBELL') {
      flagBox.innerHTML = `<span class="doorbell-banner">🔔 DOORBELL DETECTED (${evtName} / ${evtScore}) 🔔</span>`;
    } else {
      flagBox.innerHTML = `<span style="color:#7ee787;font-weight:bold;">🟢 NORMAL (No Event)</span>`;
    }

    const eventBox = document.getElementById('event');
    if (a.event && a.event !== '-') {
      eventBox.innerHTML = `<span style="color:#58a6ff;font-weight:bold;">🎵 ${a.event} (${(a.event_score??0).toFixed(2)})</span>`;
    } else {
      eventBox.textContent = '-';
    }

    show('transcript', a.text || '-');
    show('live_transcript', a.live_text || '-');
    show('error', [v.error, a.error, m.error].filter(Boolean).join('\\n'));
  }catch(e){show('error', String(e))}
}

async function setMode(mode){
  await fetch('/set_mode', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: mode})
  });
  poll();
}

async function sendCmd(cmd){
  await fetch('/manual_command', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command: cmd})
  });
  poll();
}

async function emergencyStop(){
  await fetch('/emergency_stop', {method: 'POST'});
  poll();
}

const activeKeys = new Set();
let lastSentCmd = '';

function processCombination() {
  if (activeKeys.has('b') || activeKeys.has(' ')) {
    if (lastSentCmd !== 'B') { sendCmd('B'); lastSentCmd = 'B'; }
    return;
  }
  const hasW = activeKeys.has('w');
  const hasS = activeKeys.has('s');
  const hasA = activeKeys.has('a');
  const hasD = activeKeys.has('d');

  let cmd = 'B';
  if (hasW && hasD) cmd = 'WD';
  else if (hasW && hasA) cmd = 'WA';
  else if (hasS && hasD) cmd = 'SD';
  else if (hasS && hasA) cmd = 'SA';
  else if (hasW) cmd = 'W';
  else if (hasS) cmd = 'S';
  else if (hasA) cmd = 'A';
  else if (hasD) cmd = 'D';

  if (cmd !== lastSentCmd) {
    sendCmd(cmd);
    lastSentCmd = cmd;
  }
}

document.addEventListener('keydown', (e) => {
  if (currentMode !== 'MANUAL') return;
  const k = e.key.toLowerCase();
  if (['w', 'a', 's', 'd', 'b', ' '].includes(k)) {
    activeKeys.add(k);
    processCombination();
  }
});

document.addEventListener('keyup', (e) => {
  if (currentMode !== 'MANUAL') return;
  const k = e.key.toLowerCase();
  if (['w', 'a', 's', 'd', 'b', ' '].includes(k)) {
    activeKeys.delete(k);
    processCombination();
  }
});

setInterval(poll, 400);
poll();
</script>
</main></body></html>"""


class StreamingHandler(BaseHTTPRequestHandler):
    detector = None
    audio = None
    status_provider = None
    emergency_stop = None
    set_mode_callback = None
    manual_cmd_callback = None
    audio_event_callback = None
    transcribe_text_callback = None

    def log_message(self, _format, *_args):
        return

    def _bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value):
        self._bytes(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._bytes(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/status":
            provider = type(self).status_provider
            value = {} if provider is None else provider()
            self._json(200, value)
            return
        if path == "/video_feed":
            if self.detector is None:
                self.send_error(503, "vision disabled")
                return
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            try:
                while True:
                    frame = self.detector.get_jpeg_frame()
                    if frame is not None:
                        self.wfile.write(b"--FRAME\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                        )
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.05)
            except (ConnectionError, OSError):
                pass
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/emergency_stop":
            callback = type(self).emergency_stop
            if callback is not None:
                callback()
            self._json(200, {"ok": True})
            return

        if path == "/set_mode":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                mode = data.get("mode", "AUTO")
                callback = type(self).set_mode_callback
                if callback is not None:
                    callback(mode)
                self._json(200, {"ok": True, "mode": mode})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        if path == "/manual_command":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                cmd = data.get("command", "B")
                callback = type(self).manual_cmd_callback
                if callback is not None:
                    callback(cmd)
                self._json(200, {"ok": True, "command": cmd})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        if path == "/trigger_audio_event":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                event = data.get("event", "alarm")
                score = float(data.get("score", 1.0))
                callback = type(self).audio_event_callback
                if callback is not None:
                    callback(event, score)
                self._json(200, {"ok": True, "event": event, "score": score})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        if path == "/transcribe_text":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                text = data.get("text", "")
                is_live = bool(data.get("live", False))
                callback = type(self).transcribe_text_callback
                if callback is not None:
                    callback(text, is_live)
                self._json(200, {"ok": True, "text": text, "live": is_live})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        self.send_error(404)


class RobotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class WebStreamServer:
    def __init__(
        self,
        detector_engine,
        audio_pipeline=None,
        robot_status_provider=None,
        emergency_stop=None,
        set_mode_callback=None,
        manual_cmd_callback=None,
        audio_event_callback=None,
        transcribe_text_callback=None,
        host="0.0.0.0",
        port=8080,
    ):
        StreamingHandler.detector = detector_engine
        StreamingHandler.audio = audio_pipeline
        StreamingHandler.status_provider = robot_status_provider
        StreamingHandler.emergency_stop = emergency_stop
        StreamingHandler.set_mode_callback = set_mode_callback
        StreamingHandler.manual_cmd_callback = manual_cmd_callback
        StreamingHandler.audio_event_callback = audio_event_callback
        StreamingHandler.transcribe_text_callback = transcribe_text_callback
        self.server = RobotHTTPServer((host, port), StreamingHandler)
        self.thread = None
        self.host = host
        self.port = port

    def start(self):
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="web",
            daemon=True,
        )
        self.thread.start()
        actual_port = self.server.server_address[1]
        print(f"[Web] Dual-Mode Monitor listening on http://{self.host}:{actual_port}/")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
