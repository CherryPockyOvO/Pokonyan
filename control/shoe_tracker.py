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
        pulse_pause_sec=0.10,
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
        """核心追蹤週期函數：當視野出現鞋子框時，根據目標大小自適應選擇平滑弧線或步進轉向。"""
        now = time.monotonic()
        with self.lock:
            if target is None:
                self.reset()
                return (0, 0), "No target for tracking"

            raw_dx = target["center_x"] - self.target_center_x
            height_ratio = target.get("height_ratio", 0.0)

            # -------------------------------------------------------------
            # 1. 低通指數平滑消抖算法 (Low-Pass Exponential Anti-Jitter Filter)
            # -------------------------------------------------------------
            if not self.has_smoothed:
                self.smoothed_dx = raw_dx
                self.has_smoothed = True
            else:
                self.smoothed_dx = self.alpha * raw_dx + (1.0 - self.alpha) * self.smoothed_dx

            dx = self.smoothed_dx
            valid_dist = distance_cm is not None and distance_cm > 0.0

            # -------------------------------------------------------------
            # 2. 抵達鞋子特判：持續前進 (220, 220)
            # -------------------------------------------------------------
            is_at_shoe = (
                (valid_dist and distance_cm <= self.stop_dist_cm)
                or height_ratio >= self.full_shoe_height_ratio
            )
            if is_at_shoe:
                dist_str = f"{distance_cm:.1f}cm" if valid_dist else "N/A"
                self.reason = f"💥 Bumping into shoe repeatedly! (Distance: {dist_str}, Height: {height_ratio*100:.1f}%)"
                return (220, 220), self.reason

            # -------------------------------------------------------------
            # 3. 雙階段自適應追蹤演算法 (Dual-Stage Adaptive Steering)
            # -------------------------------------------------------------
            is_far_target = height_ratio < 0.25  # 鞋子較小 / 較遠 (畫面高度佔比 < 25%)

            if is_far_target:
                self.pulse_state = "IDLE"  # 遠距離使用連續平滑弧線
                if abs(dx) <= self.deadband_px:
                    cmd = (220, 220)
                    self.reason = f"🎯 Far Tracking (Height {height_ratio*100:.0f}%): Centered (dx={dx:.1f}) -> Drive Forward"
                elif dx < 0:
                    cmd = (80, 220)  # 左前弧線 WA (80, 220)
                    self.reason = f"🎯 Far Tracking (Height {height_ratio*100:.0f}%): Curved Left (WA: 80, 220, dx={dx:.1f})"
                else:
                    cmd = (220, 80)  # 右前弧線 WD (220, 80)
                    self.reason = f"🎯 Far Tracking (Height {height_ratio*100:.0f}%): Curved Right (WD: 220, 80, dx={dx:.1f})"
                return cmd, self.reason

            # ---------------------------------------------------------
            # 階段 B: 近距離 / 大目標 (Height >= 25%)
            # ---------------------------------------------------------
            if self.pulse_state == "PULSING":
                if now < self.pulse_until:
                    return self.pulse_cmd, self.reason
                else:
                    self.pulse_state = "PAUSING"
                    self.pause_until = now + self.pulse_pause_sec
                    return (0, 0), "🎯 Near Tracking: Steering pulse pause (reading frame)"

            if self.pulse_state == "PAUSING":
                if now < self.pause_until:
                    return (0, 0), "🎯 Near Tracking: Steering pulse pause (reading frame)"
                else:
                    self.pulse_state = "IDLE"

            if abs(dx) <= self.deadband_px:
                cmd = (220, 220)
                self.reason = f"🎯 Near Tracking (Height {height_ratio*100:.0f}%): Centered (dx={dx:.1f}) -> Drive Forward"
                return cmd, self.reason

            if dx < -90.0:
                cmd = (-200, 200)
                act_name = "Step Spin Left (-200, 200)"
            elif dx < -self.deadband_px:
                cmd = (80, 220)
                act_name = "Step Curve Left (80, 220)"
            elif dx > 90.0:
                cmd = (200, -200)
                act_name = "Step Spin Right (200, -200)"
            else:
                cmd = (220, 80)
                act_name = "Step Curve Right (220, 80)"

            self.pulse_state = "PULSING"
            self.pulse_cmd = cmd
            self.pulse_until = now + (0.18 if abs(dx) <= 90 else self.pulse_duration_sec)
            self.reason = f"🎯 Near Tracking (Height {height_ratio*100:.0f}%): {act_name} (dx={dx:.1f})"

            return self.pulse_cmd, self.reason
