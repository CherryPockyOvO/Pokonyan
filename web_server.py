# -*- coding: utf-8 -*-
"""Small threaded web monitor with video, state, transcript, and emergency stop."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shoe Robot Monitor</title>
<style>
body{margin:0;background:#0d1117;color:#c9d1d9;font:16px system-ui,sans-serif}
main{max-width:900px;margin:auto;padding:18px}
h1{font-size:24px;color:#58a6ff}
img{width:100%;background:#000;border:1px solid #30363d;border-radius:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.label{color:#8b949e;font-size:13px}.value{font-size:20px;margin:4px 0 10px}
#state{color:#7ee787}#error{color:#ff7b72;white-space:pre-wrap}
button{background:#da3633;color:white;border:0;border-radius:6px;padding:12px 18px;font-weight:bold;cursor:pointer}
</style>
</head>
<body><main>
<h1>Raspberry Pi Shoe Robot</h1>
<img src="/video_feed" alt="YOLO camera stream">
<div class="grid">
  <section class="card">
    <div class="label">Mission</div><div id="state" class="value">STARTING</div>
    <div class="label">Decision</div><div id="reason">-</div>
    <div class="label">Wheel target</div><div id="command">0 / 0 RPM</div>
  </section>
  <section class="card">
    <div class="label">Shoe target</div><div id="target" class="value">not seen</div>
    <div class="label">Ultrasonic</div><div id="distance">-</div>
    <div class="label">Arduino</div><div id="arduino">-</div>
  </section>
  <section class="card">
    <div class="label">Latest YAMNet event</div><div id="event" class="value">-</div>
    <div class="label">English Whisper transcript</div><div id="transcript">-</div>
  </section>
  <section class="card">
    <button onclick="emergencyStop()">EMERGENCY STOP</button>
    <div id="error"></div>
  </section>
</div>
<script>
const show=(id,value)=>document.getElementById(id).textContent=value;
async function poll(){
  try{
    const s=await (await fetch('/status',{cache:'no-store'})).json();
    const r=s.robot||{},v=s.vision||{},a=s.audio||{},m=s.motor||{};
    show('state',r.state||'MONITOR ONLY'); show('reason',r.reason||'-');
    show('command',(r.command_left??0)+' / '+(r.command_right??0)+' RPM');
    const t=v.target;
    show('target',t?'x='+t.centre_x.toFixed(2)+', height='+t.height_ratio.toFixed(2):'not seen');
    show('distance',m.distance_cm==null?'-':m.distance_cm.toFixed(1)+' cm');
    show('arduino',m.ready?'READY':(m.connected?'WAITING':'OFFLINE'));
    show('event',a.event?(a.event+' ('+(a.event_score??0).toFixed(2)+')'):'-');
    show('transcript',a.text||'-');
    show('error',[v.error,a.error,m.error].filter(Boolean).join('\\n'));
  }catch(e){show('error',String(e))}
}
async function emergencyStop(){
  await fetch('/emergency_stop',{method:'POST'});
  poll();
}
setInterval(poll,500);poll();
</script>
</main></body></html>"""


class StreamingHandler(BaseHTTPRequestHandler):
    detector = None
    audio = None
    status_provider = None
    emergency_stop = None

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
        if path == "/audio_status":
            value = (
                {"text": "", "event": "", "gate": "CLOSED"}
                if self.audio is None
                else self.audio.get_status()
            )
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
        if self.path.split("?", 1)[0] != "/emergency_stop":
            self.send_error(404)
            return
        callback = type(self).emergency_stop
        if callback is not None:
            callback()
        self._json(200, {"ok": True})


class RobotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class WebStreamServer:
    def __init__(
        self,
        detector_engine,
        audio_pipeline=None,
        robot_status_provider=None,
        emergency_stop=None,
        host="0.0.0.0",
        port=8080,
    ):
        StreamingHandler.detector = detector_engine
        StreamingHandler.audio = audio_pipeline
        StreamingHandler.status_provider = robot_status_provider
        StreamingHandler.emergency_stop = emergency_stop
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
        print(f"[Web] Monitor listening on http://{self.host}:{actual_port}/")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
