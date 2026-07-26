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

        # 2. 獨立 YOLO 檢測框中心抗抖動追蹤控制器
        self.shoe_tracker = ShoeTrackerController(
            target_center_x=320.0,
            deadband_px=30.0,
            smoothing_alpha=0.3,
            stop_dist_cm=15.0,
            full_shoe_height_ratio=0.35,
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
        now = time.monotonic()
        with self.lock:
            self.alert = event
            self.alert_score = score
            self._transition("TRACKING_SHOE", now, f"Audio event triggered shoe tracking mission: {event}")
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

            if self.state == "E_STOP":
                return self._stop(self.reason)

            if not motor_status.get("ready", True):
                return self._stop("Arduino telemetry unavailable")

            distance = motor_status.get("distance_cm")
            bumper_pressed = motor_status.get("bumper_pressed", False)  # True if B1, False if B0

            # -------------------------------------------------------------
            # 1. 撞到鞋子狀態 (HIT_SHOE): 保持停留 5 秒
            # -------------------------------------------------------------
            if self.state == "HIT_SHOE":
                if elapsed < 5.0:
                    remaining = 5.0 - elapsed
                    self.command = (0, 0)
                    self.reason = f"👟 Reached shoe & B1 bumper triggered! Holding position 5s ({remaining:.1f}s remaining)..."
                    return self.command
                else:
                    # 5 秒時間到：重置回 IDLE 隨機漫遊，清除警報
                    self.alert = ""
                    self.alert_score = 0.0
                    self._transition("IDLE", now, "5s shoe hold complete -> Returning to ordinary random wander")
                    self.pet_wander.reset()
                    self.shoe_tracker.reset()
                    return (0, 0)

            # -------------------------------------------------------------
            # 2. 警報/門鈴觸發後的追蹤鞋子狀態 (TRACKING_SHOE)
            # -------------------------------------------------------------
            if self.state == "TRACKING_SHOE":
                # 判斷是否抵達並撞到鞋子 (YOLO 檢測鞋子面積大 AND 串口返回 B1 碰撞)
                is_large_shoe = False
                if target is not None:
                    h_ratio = target.get("height_ratio", 0.0)
                    w_ratio = target.get("width_ratio", 0.0)
                    # 鞋子面積大：高度佔比 >= 35% 或 面積佔比 >= 12%
                    is_large_shoe = (h_ratio >= 0.35 or (w_ratio * h_ratio) >= 0.12)

                if is_large_shoe and bumper_pressed:
                    self._transition("HIT_SHOE", now, "👟 Large shoe detected & Bumper B1 pressed! Holding 5s.")
                    self.command = (0, 0)
                    return self.command

                # 若 YOLO 有檢測到鞋子目標，執行 ShoeTracker
                if target is not None:
                    cmd, track_reason = self.shoe_tracker.tick(target, distance)
                    self.command = cmd
                    self.reason = track_reason
                    return self.command

                # 追蹤模式下若暫時無目標，邊漫遊避障尋找鞋子
                cmd, wander_reason = self.pet_wander.tick(distance)
                self.command = cmd
                self.reason = f"Seeking shoe (AUTO tracking): {wander_reason}"
                return self.command

            # -------------------------------------------------------------
            # 3. 普通 IDLE 隨機漫遊狀態 (NORMAL)
            # -------------------------------------------------------------
            cmd, wander_reason = self.pet_wander.tick(distance)
            self.command = cmd
            self.reason = wander_reason
            return self.command

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
