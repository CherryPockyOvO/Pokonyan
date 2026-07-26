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
        self.ever_saw_large_shoe = False
        self.shoe_tracking_enabled = True  # 是否啟動拖鞋追蹤功能（預設開啟）

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

    def set_shoe_tracking(self, enabled):
        with self.lock:
            self.shoe_tracking_enabled = bool(enabled)
            print(f"[AutoController] 👟 拖鞋追蹤功能開關切換 -> enabled={self.shoe_tracking_enabled}")
            # 切換開關時重置為乾淨 IDLE 狀態，清空先決條件位，準備接收下一次聲響任務
            self.alert = ""
            self.alert_score = 0.0
            self.ever_saw_large_shoe = False
            self.pet_wander.reset()
            self.shoe_tracker.reset()
            if self.state != "IDLE":
                self._transition("IDLE", time.monotonic(), f"Shoe tracking switch set to {self.shoe_tracking_enabled} -> Reset to IDLE")
            return self.shoe_tracking_enabled

    def _transition(self, state, now, reason):
        if state != self.state:
            print(f"[AutoController] {self.state} -> {state}: {reason}")
            self.state = state
            self.state_since = now
            if state == "IDLE":
                self.pet_wander.reset()
                self.shoe_tracker.reset()
                self.ever_saw_large_shoe = False
        self.reason = reason

    def trigger(self, event, score):
        now = time.monotonic()
        with self.lock:
            if not self.shoe_tracking_enabled:
                print(f"[AutoController] 👟 收到聲響 '{event}'，但用戶已關閉拖鞋追蹤功能 -> 忽略追蹤任務")
                return False
            self.alert = event
            self.alert_score = score
            self.ever_saw_large_shoe = False  # 觸發新聲響任務時重置先決條件標誌
            self._transition("TRACKING_SHOE", now, f"Audio event triggered shoe tracking mission: {event}")
            return True

    def emergency_stop(self):
        with self.lock:
            self._transition("E_STOP", time.monotonic(), "emergency stop triggered")
            self.command = (0, 0)
            self.pet_wander.reset()
            self.shoe_tracker.reset()
            self.ever_saw_large_shoe = False

    def reset(self):
        with self.lock:
            self.alert = ""
            self.alert_score = 0.0
            self.scan_steps = 0
            self.command = (0, 0)
            self.ever_saw_large_shoe = False
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
            # 1. 撞到鞋子狀態 (HIT_SHOE): 保持停留 5 秒，之後恢復 AUTO 模式與 NORMAL 狀態
            # -------------------------------------------------------------
            if self.state == "HIT_SHOE":
                if elapsed < 5.0:
                    remaining = 5.0 - elapsed
                    self.command = (0, 0)
                    self.reason = f"👟 Valid shoe hit! Holding position 5s ({remaining:.1f}s remaining)..."
                    return self.command
                else:
                    # 5 秒時間到：重置回 IDLE 隨機漫遊 (AUTO 模式)，清除警報，並自動將追蹤功能切換為 OFF 關閉
                    self.alert = ""
                    self.alert_score = 0.0
                    self.ever_saw_large_shoe = False
                    self.shoe_tracking_enabled = False  # 撞鞋任務完成，網頁按鈕自動切換為 OFF 停止追蹤
                    self._transition("IDLE", now, "5s shoe hold complete -> Resuming ordinary AUTO wander & turning shoe tracking OFF")
                    self.pet_wander.reset()
                    self.shoe_tracker.reset()
                    return (0, 0)

            # -------------------------------------------------------------
            # 2. 拖鞋追蹤與避障屏蔽邏輯（僅在【追蹤功能開啟 self.shoe_tracking_enabled == True】且有聲響任務時執行）
            # -------------------------------------------------------------
            is_tracking_active = self.shoe_tracking_enabled and (self.state == "TRACKING_SHOE" or bool(self.alert))

            if is_tracking_active:
                # A) 檢測鏡頭中是否出現過大鞋子（先決條件標誌位）
                if target is not None:
                    h_ratio = target.get("height_ratio", 0.0)
                    w_ratio = target.get("width_ratio", 0.0)
                    if h_ratio >= 0.20 or (w_ratio * h_ratio) >= 0.05:
                        if not self.ever_saw_large_shoe:
                            print("[AutoController] 🎯 Large shoe detected in camera frame! Prerequisite fulfilled.")
                            self.ever_saw_large_shoe = True

                # B) 撞擊有效性判斷：必須滿足【曾經出現過大鞋子 (ever_saw_large_shoe == True)】，碰撞 B1 才有效！
                if self.ever_saw_large_shoe and bumper_pressed:
                    self._transition("HIT_SHOE", now, "👟 Shoe collision valid! (Prerequisite large shoe seen & B1 bumper pressed) -> Holding 5s.")
                    self.command = (0, 0)
                    return self.command

                # C) 專注衝刺追蹤拖鞋（關閉避障，直到撞擊目標 B1）：
                if target is not None:
                    cmd, track_reason = self.shoe_tracker.tick(target, distance)
                    self.command = cmd
                    self.reason = f"[No-Obstacle Pursuit | Large Shoe Seen: {self.ever_saw_large_shoe}] {track_reason}"
                    return self.command
                else:
                    cmd = (220, 220)
                    self.command = cmd
                    self.reason = f"Seeking target (No-Obstacle Pursuit | Large Shoe Seen: {self.ever_saw_large_shoe}): Driving forward (220, 220)"
                    return self.command

            # -------------------------------------------------------------
            # 3. 追蹤關閉 或 無聲響任務：普通 IDLE 漫遊 (默認 65cm 超聲波自動避障)
            # -------------------------------------------------------------
            self.ever_saw_large_shoe = False
            cmd, wander_reason = self.pet_wander.tick(distance)
            self.command = cmd
            self.reason = f"[Ordinary Wander | Shoe Tracking OFF] {wander_reason}"
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
                "shoe_tracking_enabled": self.shoe_tracking_enabled,
            }
