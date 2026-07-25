# -*- coding: utf-8 -*-
"""Automatic state machine controller for audio/vision triggered missions with Pet Free Wandering & Real-time YOLO Center Tracking."""

import threading
import time

from control.pet_wander import PetWanderController


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

        # 寵物自由漫遊與 65cm 超聲波自動避障控制器
        self.pet_wander = PetWanderController(
            obstacle_dist_cm=65.0,
            clear_dist_cm=70.0,
        )

    def _transition(self, state, now, reason):
        if state != self.state:
            print(f"[AutoController] {self.state} -> {state}: {reason}")
            self.state = state
            self.state_since = now
            if state == "IDLE":
                self.pet_wander.reset()
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

    def reset(self):
        with self.lock:
            self.alert = ""
            self.alert_score = 0.0
            self.scan_steps = 0
            self.command = (0, 0)
            self._transition("IDLE", time.monotonic(), "reset to IDLE (AUTO mode)")
            self.pet_wander.reset()

    def _stop(self, reason):
        self.command = (0, 0)
        self.reason = reason
        return self.command

    def _track_target(self, target, distance_cm):
        """根據 YOLO 檢測框中心 (center_x) 進行即時視覺自動對準與追蹤。
        
        畫面寬度：640，中心 X = 320.0
        對準死區 Tolerance：±35 像素 (285 <= center_x <= 355 視為精確對準)
        """
        center_x = target["center_x"]
        delta_x = center_x - 320.0  # 偏左為負，偏右為正
        valid_dist = distance_cm is not None and distance_cm > 0.0

        # 超聲波小於等於 15cm，即視為成功抵達目標
        if valid_dist and distance_cm <= 15.0:
            self.command = (0, 0)
            self.reason = f"🎯 Target reached! Distance: {distance_cm:.1f} cm <= 15cm"
            return self.command

        # 根據偏離中心點 X 進行即時動態微調轉向
        if delta_x < -120.0:
            # 大幅偏左 -> 原地左轉 (-160, 165)
            self.command = (-160, 165)
            self.reason = f"🎯 YOLO Center Tracking: Target far left (dx={delta_x:.0f}) -> Spin Left"
        elif delta_x < -35.0:
            # 輕微偏左 -> 弧線左前 (75, 240)
            self.command = (75, 240)
            self.reason = f"🎯 YOLO Center Tracking: Target left (dx={delta_x:.0f}) -> Curve Left"
        elif delta_x > 120.0:
            # 大幅偏右 -> 原地右轉 (175, -175)
            self.command = (175, -175)
            self.reason = f"🎯 YOLO Center Tracking: Target far right (dx={delta_x:.0f}) -> Spin Right"
        elif delta_x > 35.0:
            # 輕微偏右 -> 弧線右前 (240, 80)
            self.command = (240, 80)
            self.reason = f"🎯 YOLO Center Tracking: Target right (dx={delta_x:.0f}) -> Curve Right"
        else:
            # 精確對準中心 (±35 像素) -> 直向前進 (200, 200)
            self.command = (200, 200)
            self.reason = f"🎯 YOLO Center Tracking: Target Centered (dx={delta_x:.0f}) -> Drive Forward"

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
            # AUTO 模式下優先檢測：若 YOLO 辨識到拖鞋，優先進行中心追蹤 (YOLO Center Tracking)
            # -------------------------------------------------------------
            if target is not None:
                return self._track_target(target, distance)

            # -------------------------------------------------------------
            # IDLE / 無目標狀態：執行寵物自由漫遊與 65cm 超聲波自動避障
            # -------------------------------------------------------------
            if self.state in ("IDLE", "SEARCH_PAUSE", "SEARCH_TURN"):
                cmd, wander_reason = self.pet_wander.tick(distance)
                self.command = cmd
                self.reason = wander_reason
                return self.command

            if self.state == "FORWARD":
                if elapsed >= self.forward_seconds:
                    self._transition("DONE", now, "timed forward run complete")
                    return self._stop(self.reason)
                return self._track_target(target, distance) if target is not None else self._stop(self.reason)

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
