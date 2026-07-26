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
body{margin:0;background:#0d1117;color:#c9d1d9;font:16px system-ui,sans-serif}
main{max-width:960px;margin:auto;padding:18px}
h1{font-size:24px;color:#58a6ff;margin-bottom:14px}
img{width:100%;background:#000;border:1px solid #30363d;border-radius:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.label{color:#8b949e;font-size:13px}.value{font-size:20px;margin:4px 0 10px;font-weight:600}
#state{color:#7ee787}#mode-val{color:#58a6ff}#error{color:#ff7b72;white-space:pre-wrap;margin-top:10px}
.mode-btn-group{display:flex;gap:10px;margin-bottom:14px}
.btn{border:0;border-radius:6px;padding:10px 16px;font-weight:bold;cursor:pointer;transition:all 0.2s}
.btn-mode{background:#21262d;color:#c9d1d9;border:1px solid #30363d}
.btn-mode.active{background:#238636;color:white;border-color:#2ea043}
.btn-stop{background:#da3633;color:white;width:100%;padding:14px;font-size:16px;margin-top:10px}
.btn-stop:hover{background:#f85149}
.wasd-pad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:260px;margin:12px auto}
.btn-wasd{background:#21262d;color:white;font-size:18px;padding:16px;border:1px solid #30363d;border-radius:6px}
.btn-wasd:active,.btn-wasd.active{background:#1f6feb;border-color:#388bfd}
.btn-brake{background:#8b949e;color:#0d1117}
.btn-brake:active,.btn-brake.active{background:#ff7b72;color:white}
.wasd-hint{font-size:12px;color:#8b949e;text-align:center;margin-top:8px}
</style>
</head>
<body><main>
<h1>🤖 Raspberry Pi 5 Shoe Robot Control</h1>
<img src="/video_feed" alt="YOLO camera stream">

<div class="grid">
  <section class="card">
    <div class="label">Control Mode</div>
    <div class="mode-btn-group">
      <button id="btn-auto" class="btn btn-mode active" onclick="setMode('AUTO')">🤖 AUTO Mode</button>
      <button id="btn-manual" class="btn btn-mode" onclick="setMode('MANUAL')">🎮 MANUAL Mode</button>
    </div>
    <div class="label">Current Status</div><div id="state" class="value">STARTING</div>
    <div class="label">Decision / Reason</div><div id="reason">-</div>
    <div class="label">Motor PWM / Direction</div><div id="command">0 / 0</div>
  </section>

  <section class="card">
    <div class="label">🎮 MANUAL WASD Control (8-Direction Keyboard / Touch)</div>
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
    <div class="wasd-hint">Keyboard: W (Forward), S (Back), A (Left), D (Right), WD/WA/SD/SA (Diagonals), B/Space (Brake)</div>
  </section>

  <section class="card">
    <div class="label">Shoe target</div><div id="target" class="value">not seen</div>
    <div class="label">YOLO Inference Rate</div><div id="yolo_fps" class="value">0.0 FPS</div>
    <div class="label">Ultrasonic Distance</div><div id="distance" class="value">-</div>
    <div class="label">Arduino Serial Status</div><div id="arduino">-</div>
  </section>

  <section class="card">
    <div class="label">Audio Event</div><div id="event" class="value">-</div>
    <div class="label">Whisper Transcript</div><div id="transcript">-</div>
    <button class="btn btn-stop" onclick="emergencyStop()">🚨 EMERGENCY STOP</button>
    <div id="error"></div>
  </section>
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
    
    const t=v.target;
    show('target', t ? 'x=' + t.centre_x.toFixed(2) + ', height=' + t.height_ratio.toFixed(2) : 'not seen');
    show('yolo_fps', (v.fps ?? 0.0).toFixed(1) + ' FPS');
    show('distance', m.distance_cm == null ? '-' : m.distance_cm.toFixed(1) + ' cm');
    show('arduino', m.ready ? 'CONNECTED (Serial)' : (m.connected ? 'WAITING' : 'OFFLINE'));
    show('event', a.event ? (a.event + ' (' + (a.event_score??0).toFixed(2) + ')') : '-');
    show('transcript', a.text || '-');
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

// ⌨️ Multi-key combination Keyboard listener (W+D -> WD, W+A -> WA, S+D -> SD, S+A -> SA)
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
                callback = type(self).transcribe_text_callback
                if callback is not None:
                    callback(text)
                self._json(200, {"ok": True, "text": text})
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
