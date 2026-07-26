# -*- coding: utf-8 -*-
"""Single entry point for the Raspberry Pi shoe-seeking robot demo."""

from pathlib import Path
import argparse
import signal
import threading
import time

from control.motor import MotorGateway
from control.auto_controller import AutoController
from control.manual_controller import ManualController
from ui.web_server import WebStreamServer, StreamingHandler


BASE_DIR = Path(__file__).resolve().parent


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


class RobotDualModeManager:
    """Coordinator managing AUTO mode (AutoController) and MANUAL mode (ManualController)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "MANUAL"  # Default to MANUAL mode on startup
        self.auto_ctrl = AutoController(forward_pwm=240, slow_pwm=240, pivot_pwm=165, inner_pwm=80, slow_distance_cm=65.0)
        self.manual_ctrl = ManualController()

    def set_mode(self, mode):
        mode = str(mode).upper()
        if mode not in ("AUTO", "MANUAL"):
            return False
        with self.lock:
            if self.mode != mode:
                print(f"[Top] Mode changed: {self.mode} -> {mode}")
                self.mode = mode
                if mode == "MANUAL":
                    self.auto_ctrl.emergency_stop()
                    self.manual_ctrl.handle_command("B")
                else:
                    self.manual_ctrl.emergency_stop()
                    self.auto_ctrl.reset()
            return True

    def handle_manual_command(self, cmd):
        if self.mode != "MANUAL":
            return False
        return self.manual_ctrl.handle_command(cmd)

    def trigger_alarm(self, event, score):
        with self.lock:
            if self.mode != "AUTO":
                print(f"[Top] Ignored audio event {event}: robot is in MANUAL mode")
                return False
            return self.auto_ctrl.trigger(event, score)

    def emergency_stop(self):
        with self.lock:
            self.auto_ctrl.emergency_stop()
            self.manual_ctrl.emergency_stop()

    def tick(self, target, motor_status):
        with self.lock:
            if self.mode == "MANUAL":
                return self.manual_ctrl.tick(motor_status)
            return self.auto_ctrl.tick(target, motor_status)

    def get_status(self):
        with self.lock:
            status = {
                "mode": self.mode,
            }
            if self.mode == "MANUAL":
                man_stat = self.manual_ctrl.get_status()
                status.update({
                    "state": "MANUAL_CONTROL",
                    "reason": man_stat["reason"],
                    "alert": "",
                    "alert_score": 0.0,
                    "scan_steps": 0,
                    "command_left": man_stat["command_left"],
                    "command_right": man_stat["command_right"],
                })
            else:
                auto_stat = self.auto_ctrl.get_status()
                status.update(auto_stat)
            return status


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--ncnn", default="model/best_ncnn_model")
    parser.add_argument("--yamnet", default="model/yamnet.tflite")
    parser.add_argument("--whisper", default="model/ggml-tiny.en.bin")
    parser.add_argument("--serial", default="/dev/ttyACM0")
    parser.add_argument("--speech-thresh", type=float, default=0.35)
    parser.add_argument("--event-thresh", type=float, default=0.45)
    parser.add_argument("--simulate-alarm", action="store_true")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--vision-only", action="store_true")
    parser.add_argument("--audio-only", action="store_true")
    args = parser.parse_args()
    if args.vision_only and args.audio_only:
        parser.error("--vision-only and --audio-only cannot be used together")
    return args


def main():
    args = parse_args()
    stopped = threading.Event()
    manager = RobotDualModeManager()
    detector = None
    audio = None
    motor = None
    web = None

    def system_ready():
        vision_ready = detector is not None and detector.get_status()["ready"]
        motor_ready = motor is not None and motor.get_status()["ready"]
        return vision_ready and motor_ready

    latest_transcript = ""
    latest_live_transcript = ""

    latest_audio_event = "-"
    latest_audio_score = 0.0
    latest_audio_ts = 0.0

    alarm_flag = False
    alarm_event_name = ""
    alarm_score = 0.0
    alarm_ts = 0.0

    def audio_event(event, score):
        nonlocal latest_audio_event, latest_audio_score, latest_audio_ts
        nonlocal alarm_flag, alarm_event_name, alarm_score, alarm_ts
        now = time.time()
        latest_audio_event = event
        latest_audio_score = score
        latest_audio_ts = now

        is_bell = any(k in event.lower() for k in ["alarm", "bell", "ring", "siren", "doorbell", "ding-dong", "chime"])
        if is_bell:
            alarm_flag = True
            alarm_event_name = event
            alarm_score = score
            alarm_ts = now
            print(f"[Top <- YAMNet] 🔔 DOORBELL/ALARM TRIGGERED: '{event}' ({score:.2f})")
            if system_ready():
                accepted = manager.trigger_alarm(event, score)
                print(f"[Top] Mission accepted: {accepted}")
            else:
                print("[Top] Mission ignored: vision or Arduino is not ready")
        else:
            print(f"[Top <- YAMNet] Real-time sound: '{event}' ({score:.2f})")

    def transcribe_text(text, is_live=False):
        nonlocal latest_transcript, latest_live_transcript
        if is_live:
            latest_live_transcript = text
            print(f"[Top <- Realtime STT Draft] '{text}'")
        else:
            latest_transcript = text
            latest_live_transcript = ""
            print(f"[Top <- Realtime STT Final] '{text}'")

        text_lower = text.lower().strip()

        # 1. 說 "what can I say" -> 切換為 MANUAL 手動模式 (實時即時判定)
        if "what can i say" in text_lower or "manual" in text_lower:
            manager.set_mode("MANUAL")
            print("[Top] 🎤 實時語音指令: 成功切換為 🎮 [MANUAL 手動模式]")

        # 2. 說 "man" -> 切換為 AUTO 自動巡航模式並啟動任務 (實時即時判定)
        elif any(k in text_lower for k in ["man", "alarm", "start", "find", "shoe", "seek", "auto"]):
            manager.set_mode("AUTO")
            print("[Top] 🎤 實時語音指令: 成功切換為 🤖 [AUTO 自動巡航模式]")
            if system_ready():
                accepted = manager.trigger_alarm("alarm", 1.0)
                print(f"[Top] 巡航尋物任務啟動: {accepted}")
            else:
                print("[Top] 巡航模式已切換 (等待相機或 Arduino 連接就緒)")

    def emergency_stop():
        manager.emergency_stop()
        if motor is not None:
            motor.emergency_stop()

    def status():
        now = time.time()
        # Auto-reset live event if no update for 2.5s
        live_event = latest_audio_event if (now - latest_audio_ts <= 2.5) else "-"
        live_score = latest_audio_score if (now - latest_audio_ts <= 2.5) else 0.0

        # Auto-reset alarm flag after 5.0s of no bell sound
        active_alarm = alarm_flag if (now - alarm_ts <= 5.0) else False

        audio_stat = {} if audio is None else audio.get_status()
        audio_stat["event"] = live_event
        audio_stat["event_score"] = live_score
        audio_stat["alarm_flag"] = active_alarm
        audio_stat["alarm_event"] = alarm_event_name if active_alarm else ""
        audio_stat["alarm_score"] = alarm_score if active_alarm else 0.0

        if latest_transcript:
            audio_stat["text"] = latest_transcript
        if latest_live_transcript:
            audio_stat["live_text"] = latest_live_transcript

        return {
            "robot": manager.get_status(),
            "vision": {} if detector is None else detector.get_status(),
            "audio": audio_stat,
            "motor": {} if motor is None else motor.get_status(),
        }

    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())

    # 1. 優先開啟 Web 8080 端口，確保即使硬體未連接也能訪問 Web 界面
    try:
        web = WebStreamServer(
            detector_engine=None,
            robot_status_provider=status,
            emergency_stop=emergency_stop,
            set_mode_callback=manager.set_mode,
            manual_cmd_callback=manager.handle_manual_command,
            audio_event_callback=audio_event,
            transcribe_text_callback=transcribe_text,
            host="0.0.0.0",
            port=args.port,
        )
        web.start()
        print(f"[Top] Web monitor listening on http://0.0.0.0:{args.port}/")
    except Exception as e:
        print(f"[Top] WebStreamServer port bind warning: {e}")

    try:
        if not args.audio_only:
            try:
                from perception.detector import YoloDetectorEngine

                detector = YoloDetectorEngine(
                    model_path=local_path(args.ncnn),
                )
                detector.start()
                if web is not None:
                    StreamingHandler.detector = detector
            except Exception as e:
                print(f"[Top] Vision detector init warning: {e}")

        if not args.vision_only and not args.audio_only:
            try:
                motor = MotorGateway(port=args.serial, dry_run=args.dry_run)
                motor.start()
            except Exception as e:
                print(f"[Top] Motor gateway init warning: {e}")
                motor = MotorGateway(dry_run=True)
                motor.start()

        if not args.vision_only and not args.no_audio:
            try:
                from perception.audio_pipeline import YamnetWhisperAudioPipeline

                audio = YamnetWhisperAudioPipeline(
                    yamnet_model_path=local_path(args.yamnet),
                    whisper_model_path=local_path(args.whisper),
                    speech_threshold=args.speech_thresh,
                    event_threshold=args.event_thresh,
                    on_event=audio_event,
                )
                audio.start()
                if web is not None:
                    StreamingHandler.audio = audio
            except Exception as e:
                print(f"[Top] Audio pipeline init warning: {e}")

        if args.simulate_alarm:
            deadline = time.monotonic() + 15.0
            while not stopped.is_set() and not system_ready() and time.monotonic() < deadline:
                time.sleep(0.1)
            if system_ready():
                manager.trigger_alarm("alarm", 1.0)
            else:
                print("[Top] Simulated alarm not started: system not ready")

        previous = None
        while not stopped.wait(0.05):
            if motor is None:
                continue
            target = None if detector is None else detector.get_target(max_age=0.5)
            command = manager.tick(target, motor.get_status())
            motor.set_target(*command)
            current = manager.get_status()
            marker = (current["mode"], current["state"], current["reason"], command)
            if marker != previous:
                print(
                    f"[Top] [{current['mode']}] {current['state']}: {current['reason']} "
                    f"PWM: {command[0]} / {command[1]}"
                )
                previous = marker
    finally:
        emergency_stop()
        if web is not None:
            web.stop()
        if audio is not None:
            audio.stop()
        if detector is not None:
            detector.stop()
        if motor is not None:
            motor.close()
        print("[Top] Stopped")


if __name__ == "__main__":
    main()
