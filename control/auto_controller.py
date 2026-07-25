# -*- coding: utf-8 -*-
"""Automatic state machine controller for audio/vision triggered missions with Pet Free Wandering & Real-time YOLO Center Tracking."""

import threading
import time

from control.pet_wander import PetWanderController
from control.shoe_tracker import ShoeTrackerController


class AutoController:
    """State machine for automatic shoe-seeking mission, pet-like free wandering, and YOLO box center tracking."""

    def __init__(self, forward_pwm=240, slow_pwm=240, pivot_pwm=165, inner_pwm=80, slow_distance_cm=65.0):
        self.lock = threading.Lock()
        self.state = "IDLE"
        self.state_since = time.monotonic()
        self.alert = ""
        self.alert_score = 0.0
        self.scan_steps = 0
        self.command = (0, 0)
        self.reason = "waiting for alarm (AUTO mode)"

        self.forward_pwm = forward_pwm
        self.slow_pwm = slow_pwm
        self.slow_distance_cm = slow_distance_cm
        self.pivot_pwm = pivot_pwm
        self.inner_pwm = inner_pwm
        self.rotate_step_seconds = 0.45
        self.scan_pause_seconds = 2.0
        self.forward_seconds = 10.0

        # 1. 寵物自由漫遊與 65cm 超聲波自動避障控制器
        self.pet_wander = PetWanderController(
            obstacle_dist_cm=65.0,
            clear_dist_cm=70.0,
        )

        # 2. 獨立 YOLO 檢測框中心抗抖動追蹤與避障控制器
        self.shoe_tracker = ShoeTrackerController(
            target_center_x=320.0,
            deadband_px=30.0,
            smoothing_alpha=0.3,
            obstacle_dist_cm=65.0,
            stop_dist_cm=15.0,
            full_shoe_height_ratio=0.65,
        )

    def _transition(self, state, now, reason):
        if state != self.state:
            print(f"[AutoController] {self.state} -> {state}: {reason}")
            self.state = state
            self.state_since = now
            if state == "IDLE":
                self.pet_wander.reset()
                self.shoe_tracker.reset()
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
            self._transition("E_STOP", time.monotonic(), "emergency stop triggered")
            self.command = (0, 0)
            self.pet_wander.reset()
            self.shoe_tracker.reset()

    def reset(self):
        with self.lock:
            self.alert = ""
            self.alert_score = 0.0
            self.scan_steps = 0
            self.command = (0, 0)
            self._transition("IDLE", time.monotonic(), "reset to IDLE (AUTO mode)")
            self.pet_wander.reset()
            self.shoe_tracker.reset()

    def _stop(self, reason):
        self.command = (0, 0)
        self.reason = reason
        return self.command

    def tick(self, target, motor_status):
        now = time.monotonic()
        with self.lock:
            elapsed = now - self.state_since

            if self.state in ("DONE", "FAILED", "E_STOP"):
                return self._stop(self.reason)

            if not motor_status["ready"]:
                self._transition("FAILED", now, "Arduino telemetry unavailable")
                return self._stop(self.reason)

            distance = motor_status["distance_cm"]

            # -------------------------------------------------------------
            # AUTO 模式下優先檢測：若 YOLO 連續 5 幀穩定辨識到拖鞋，啟動獨立 ShoeTracker
            # -------------------------------------------------------------
            if target is not None:
                cmd, track_reason = self.shoe_tracker.tick(target, distance)
                self.command = cmd
                self.reason = track_reason
                return self.command

            # -------------------------------------------------------------
            # IDLE / 無目標狀態：重置追蹤器並執行寵物自由漫遊與 65cm 超聲波自動避障
            # -------------------------------------------------------------
            self.shoe_tracker.reset()
            if self.state in ("IDLE", "SEARCH_PAUSE", "SEARCH_TURN"):
                cmd, wander_reason = self.pet_wander.tick(distance)
                self.command = cmd
                self.reason = wander_reason
                return self.command

            if self.state == "FORWARD":
                if elapsed >= self.forward_seconds:
                    self._transition("DONE", now, "timed forward run complete")
                    return self._stop(self.reason)
                cmd, track_reason = self.shoe_tracker.tick(target, distance)
                self.command = cmd
                self.reason = track_reason
                return self.command

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
