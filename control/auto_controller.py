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
            # 1. 撞到鞋子狀態 (HIT_SHOE): 保持停留 2 秒後重新啟動
            # -------------------------------------------------------------
            if self.state == "HIT_SHOE":
                if elapsed < 2.0:
                    remaining = 2.0 - elapsed
                    self.command = (0, 0)
                    self.reason = f"👟 Valid shoe hit! Holding position 2s ({remaining:.1f}s remaining)..."
                    return self.command
                else:
                    # 2 秒時間到：重置回 IDLE 隨機漫遊，清除聲響警報恢復 NORMAL，重新啟動
                    self.alert = ""
                    self.alert_score = 0.0
                    self._transition("IDLE", now, "2s shoe hold complete -> Resuming ordinary AUTO wander")
                    self.pet_wander.reset()
                    self.shoe_tracker.reset()
                    return (0, 0)

            # -------------------------------------------------------------
            # 2. 聲響觸發後的拖鞋追蹤狀態 (TRACKING_SHOE)
            # -------------------------------------------------------------
            if self.state == "TRACKING_SHOE":
                # A) 碰撞檢測：撞擊鞋子 (觸發微動開關 B1) 即進入 2 秒停留
                if bumper_pressed:
                    self._transition("HIT_SHOE", now, "👟 Shoe collision! (B1 bumper pressed) -> Holding 2s.")
                    self.command = (0, 0)
                    return self.command

                # B) 追蹤拖鞋：視野出現鞋子即直接追蹤（遠處平滑組合鍵，近處小幅度步進原地轉彎）
                if target is not None:
                    cmd, track_reason = self.shoe_tracker.tick(target, distance)
                    self.command = cmd
                    self.reason = f"[Pursuing Shoe] {track_reason}"
                    return self.command
                else:
                    # 追蹤中暫時沒看到鞋子：執行適當的原地旋轉與組合鍵尋找 (直行上限限制為 150)
                    cmd, search_reason = self.pet_wander.tick(distance, max_straight_speed=150)
                    self.command = cmd
                    self.reason = f"[Seeking Shoe (150 Max)] {search_reason}"
                    return self.command

            # -------------------------------------------------------------
            # 3. 無聲響任務：普通 IDLE 漫遊 (執行 65cm 超聲波自動避障)
            # -------------------------------------------------------------
            cmd, wander_reason = self.pet_wander.tick(distance)
            self.command = cmd
            self.reason = f"[Ordinary Wander] {wander_reason}"
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
