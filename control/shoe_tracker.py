# -*- coding: utf-8 -*-
"""Autonomous Shoe Tracking Controller with Dual-Stage Adaptive Steering & Pure Target Pursuit."""

import threading
import time


class ShoeTrackerController:
    """Shoe Tracking Controller focusing 100% on target pursuit when a YOLO shoe box is detected.
    
    Features:
    1. Dual-Stage Adaptive Steering:
       - Far / Small Shoe (height < 25%): Uses smooth combination curved turns (WA: 90, 240 / WD: 240, 90) to track without shaking!
       - Near / Large Shoe (height >= 25%): Uses step-pulse turns for precise close-range alignment before bumping into shoe.
    2. Continuous Bumping: Keeps driving forward (200, 200) into the shoe without braking upon arrival.
    3. Low-Pass Exponential Anti-Jitter Filter on target center offset (dx).
    """

    def __init__(
        self,
        target_center_x=320.0,
        deadband_px=30.0,
        smoothing_alpha=0.3,
        obstacle_dist_cm=65.0,
        stop_dist_cm=15.0,
        full_shoe_height_ratio=0.60,
        pulse_duration_sec=0.15,
        pulse_pause_sec=1.0,  # 近距離轉灣停頓改為 1.0 秒，確保鏡頭畫面穩定
        **kwargs,
    ):
        self.lock = threading.Lock()
        self.target_center_x = target_center_x
        self.deadband_px = deadband_px
        self.alpha = smoothing_alpha
        self.stop_dist_cm = stop_dist_cm
        self.full_shoe_height_ratio = full_shoe_height_ratio
        self.pulse_duration_sec = pulse_duration_sec
        self.pulse_pause_sec = pulse_pause_sec

        self.smoothed_dx = 0.0
        self.has_smoothed = False

        # 脈衝步進轉向狀態機 (Pulse Step Steering State)
        self.pulse_state = "IDLE"  # "IDLE", "PULSING", "PAUSING"
        self.pulse_cmd = (0, 0)
        self.pulse_until = 0.0
        self.pause_until = 0.0
        self.reason = "Shoe tracker initialized"

    def reset(self):
        """重置追蹤器狀態與步進脈衝狀態。"""
        with self.lock:
            self.smoothed_dx = 0.0
            self.has_smoothed = False
            self.pulse_state = "IDLE"
            self.pulse_cmd = (0, 0)
            self.pulse_until = 0.0
            self.pause_until = 0.0
            self.reason = "Shoe tracker reset"

    def tick(self, target, distance_cm):
        """核心追蹤週期函數：<10%遠距離組合鍵，10%-15%近距離降速150與1s停頓原地旋轉，>=15%停止等待撞擊。"""
        now = time.monotonic()
        with self.lock:
            if target is None:
                self.reset()
                return (0, 0), "No target for tracking"

            raw_dx = target["center_x"] - self.target_center_x
            height_ratio = target.get("height_ratio", 0.0)
            width_ratio = target.get("width_ratio", 0.0)
            area_ratio = target.get("area_ratio", height_ratio * width_ratio)
            shoe_size_ratio = max(height_ratio, area_ratio)

            # -------------------------------------------------------------
            # 1. 低通指數平滑消抖算法 (Low-Pass Exponential Anti-Jitter Filter)
            # -------------------------------------------------------------
            if not self.has_smoothed:
                self.smoothed_dx = raw_dx
                self.has_smoothed = True
            else:
                self.smoothed_dx = self.alpha * raw_dx + (1.0 - self.alpha) * self.smoothed_dx

            dx = self.smoothed_dx

            # -------------------------------------------------------------
            # 階段 1: 鞋子面積/高度達到 22% (>= 0.22) -> 停止等待撞擊 (0, 0)
            # -------------------------------------------------------------
            if shoe_size_ratio >= 0.22:
                self.pulse_state = "IDLE"
                self.reason = f"🛑 Arrived at shoe (Ratio {shoe_size_ratio*100:.1f}% >= 22%): Stopped (0, 0), waiting for B1 bumper collision"
                return (0, 0), self.reason

            # -------------------------------------------------------------
            # 階段 2: 鞋子面積/高度在 15% ~ 22% 之間 (0.15 <= Ratio < 0.22) -> 近距離模式 (實時消抖更新)
            # -------------------------------------------------------------
            if shoe_size_ratio >= 0.15:
                # 步進脈衝狀態機處理 (轉 0.15s -> 停頓 1.0s 讀幀)
                if self.pulse_state == "PULSING":
                    if now < self.pulse_until:
                        return self.pulse_cmd, self.reason
                    else:
                        self.pulse_state = "PAUSING"
                        self.pause_until = now + self.pulse_pause_sec
                        return (0, 0), "🎯 Near Mode (15%-22%): Step pulse pause 1.0s (inspecting frame)"

                if self.pulse_state == "PAUSING":
                    if now < self.pause_until:
                        return (0, 0), "🎯 Near Mode (15%-22%): Step pulse pause 1.0s (inspecting frame)"
                    else:
                        self.pulse_state = "IDLE"

                # 偏出中心門檻以外：執行原地步進旋轉 (240, -240) / (-240, 240)，旋轉後停頓 1.0 秒
                if abs(dx) > self.deadband_px:
                    if dx < 0:
                        cmd = (-240, 240)
                        act_name = "In-place Spin Left (-240, 240)"
                    else:
                        cmd = (240, -240)
                        act_name = "In-place Spin Right (240, -240)"

                    self.pulse_state = "PULSING"
                    self.pulse_cmd = cmd
                    self.pulse_until = now + self.pulse_duration_sec
                    self.reason = f"🎯 Near Mode (Ratio {shoe_size_ratio*100:.1f}%): {act_name} (dx={dx:.1f}) -> Pause 1.0s"
                    return self.pulse_cmd, self.reason

                # 對準中心：執行 (150, 150) 步進直行 0.15s，隨後停頓 1.0 秒供鏡頭重新觀察
                cmd = (150, 150)
                self.pulse_state = "PULSING"
                self.pulse_cmd = cmd
                self.pulse_until = now + self.pulse_duration_sec
                self.reason = f"🎯 Near Mode (Ratio {shoe_size_ratio*100:.1f}%): Centered -> Step Forward (150, 150) -> Pause 1.0s"
                return self.pulse_cmd, self.reason

            # -------------------------------------------------------------
            # 階段 3: 鞋子面積/高度小於 15% (< 0.15) -> 遠距離模式 (實時消抖更新，組合鍵 WA/WD 逼近)
            # -------------------------------------------------------------
            self.pulse_state = "IDLE"
            if abs(dx) <= self.deadband_px:
                cmd = (220, 220)
                self.reason = f"🎯 Far Pursuit (Ratio {shoe_size_ratio*100:.1f}% < 15%): Centered -> Drive Forward (220, 220)"
            elif dx < 0:
                cmd = (80, 220)  # 左前弧線 WA (80, 220)
                self.reason = f"🎯 Far Pursuit (Ratio {shoe_size_ratio*100:.1f}% < 15%): Curved Left (WA: 80, 220)"
            else:
                cmd = (220, 80)  # 右前弧線 WD (220, 80)
                self.reason = f"🎯 Far Pursuit (Ratio {shoe_size_ratio*100:.1f}% < 15%): Curved Right (WD: 220, 80)"
            return cmd, self.reason
