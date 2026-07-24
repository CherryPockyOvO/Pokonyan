# -*- coding: utf-8 -*-
"""Single entry point for the Raspberry Pi shoe-seeking robot demo."""

from pathlib import Path
import argparse
import signal
import threading
import time

from motor import MotorGateway
from web_server import WebStreamServer


BASE_DIR = Path(__file__).resolve().parent


def local_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


class RobotController:
    """Small state machine: alert -> initial check/scan -> timed drive."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "IDLE"
        self.state_since = time.monotonic()
        self.alert = ""
        self.alert_score = 0.0
        self.scan_steps = 0
        self.command = (0, 0)
        self.reason = "waiting for alarm"

        self.forward_rpm = 60
        self.slow_rpm = 40
        self.slow_distance_cm = 15.0
        self.pivot_rpm = 65
        self.rotate_step_seconds = 0.45
        self.scan_pause_seconds = 2.0
        self.forward_seconds = 10.0

    def _transition(self, state, now, reason):
        if state != self.state:
            print(f"[Robot] {self.state} -> {state}: {reason}")
            self.state = state
            self.state_since = now
        self.reason = reason

    def trigger(self, event, score):
        if event not in ("alarm", "alarm_clock"):
            return False
        now = time.monotonic()
        with self.lock:
            if self.state not in ("IDLE", "DONE", "FAILED"):
                return False
            self.alert = event
            self.alert_score = score
            self.scan_steps = 0
            self._transition("SEARCH_PAUSE", now, f"initial camera check: {event}")
            return True

    def emergency_stop(self):
        with self.lock:
            self._transition("E_STOP", time.monotonic(), "web/operator emergency stop")
            self.command = (0, 0)

    def _stop(self, reason):
        self.command = (0, 0)
        self.reason = reason
        return self.command

    def _forward(self, distance):
        near = distance is not None and distance <= self.slow_distance_cm
        rpm = self.slow_rpm if near else self.forward_rpm
        self.command = (rpm, rpm)
        self.reason = "distance at or below 15 cm" if near else "timed forward run"
        return self.command

    def tick(self, target, motor_status):
        now = time.monotonic()
        with self.lock:
            elapsed = now - self.state_since

            if self.state in ("IDLE", "DONE", "FAILED", "E_STOP"):
                return self._stop(self.reason)

            if not motor_status["ready"]:
                self._transition("FAILED", now, "Arduino telemetry unavailable")
                return self._stop(self.reason)

            distance = motor_status["distance_cm"]

            if self.state == "SEARCH_TURN":
                if target is not None:
                    self._transition("FORWARD", now, "shoe detected")
                    return self._forward(distance)
                if elapsed >= self.rotate_step_seconds:
                    self.scan_steps += 1
                    self._transition("SEARCH_PAUSE", now, "45 degree step complete")
                    return self._stop("camera pause")
                self.command = (-self.pivot_rpm, self.pivot_rpm)
                self.reason = f"scan step {self.scan_steps + 1}"
                return self.command

            if self.state == "SEARCH_PAUSE":
                if target is not None:
                    self._transition("FORWARD", now, "shoe detected")
                    return self._forward(distance)
                if elapsed >= self.scan_pause_seconds:
                    self._transition("SEARCH_TURN", now, "continue scan")
                return self._stop("camera pause")

            if self.state == "FORWARD":
                if elapsed >= self.forward_seconds:
                    self._transition("DONE", now, "timed forward run complete")
                    return self._stop(self.reason)
                return self._forward(distance)

            self._transition("FAILED", now, "unknown state")
            return self._stop(self.reason)

    def get_status(self):
        with self.lock:
            return {
                "state": self.state,
                "reason": self.reason,
                "alert": self.alert,
                "alert_score": round(self.alert_score, 3),
                "scan_steps": self.scan_steps,
                "command_left": self.command[0],
                "command_right": self.command[1],
            }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--cam", type=int, default=0)
    parser.add_argument("--ncnn", default="best_ncnn_model")
    parser.add_argument("--yamnet", default="yamnet.tflite")
    parser.add_argument("--whisper", default="ggml-tiny.en.bin")
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
    controller = RobotController()
    detector = None
    audio = None
    motor = None
    web = None

    def system_ready():
        vision_ready = detector is not None and detector.get_status()["ready"]
        motor_ready = motor is not None and motor.get_status()["ready"]
        return vision_ready and motor_ready

    def audio_event(event, score):
        print(f"[Top] Audio event: {event} ({score:.2f})")
        if event in ("alarm", "alarm_clock"):
            if system_ready():
                accepted = controller.trigger(event, score)
                print(f"[Top] Mission accepted: {accepted}")
            else:
                print("[Top] Mission ignored: vision or Arduino is not ready")

    def emergency_stop():
        controller.emergency_stop()
        if motor is not None:
            motor.emergency_stop()

    def status():
        return {
            "robot": controller.get_status(),
            "vision": {} if detector is None else detector.get_status(),
            "audio": {} if audio is None else audio.get_status(),
            "motor": {} if motor is None else motor.get_status(),
        }

    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())

    try:
        if not args.audio_only:
            from detector import YoloDetectorEngine

            detector = YoloDetectorEngine(
                model_path=local_path(args.ncnn),
                camera_id=args.cam,
            )
            detector.start()

        if not args.vision_only and not args.audio_only:
            motor = MotorGateway(port=args.serial, dry_run=args.dry_run)
            motor.start()

        if not args.vision_only and not args.no_audio:
            from audio_pipeline import YamnetWhisperAudioPipeline

            audio = YamnetWhisperAudioPipeline(
                yamnet_model_path=local_path(args.yamnet),
                whisper_model_path=local_path(args.whisper),
                speech_threshold=args.speech_thresh,
                event_threshold=args.event_thresh,
                on_event=audio_event,
            )
            audio.start()

        web = WebStreamServer(
            detector_engine=detector,
            robot_status_provider=status,
            emergency_stop=emergency_stop,
            host="0.0.0.0",
            port=args.port,
        )
        web.start()
        print(f"[Top] Web monitor: http://<RaspberryPi-IP>:{args.port}/")

        if args.simulate_alarm:
            deadline = time.monotonic() + 15.0
            while not stopped.is_set() and not system_ready() and time.monotonic() < deadline:
                time.sleep(0.1)
            if system_ready():
                controller.trigger("alarm", 1.0)
            else:
                print("[Top] Simulated alarm not started: system not ready")

        previous = None
        while not stopped.wait(0.05):
            if motor is None:
                continue
            target = None if detector is None else detector.get_target(max_age=0.5)
            command = controller.tick(target, motor.get_status())
            motor.set_target(*command)
            current = controller.get_status()
            marker = (current["state"], current["reason"], command)
            if marker != previous:
                print(
                    f"[Top] {current['state']}: {current['reason']} "
                    f"V {command[0]} {command[1]}"
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
