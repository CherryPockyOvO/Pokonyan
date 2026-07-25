# -*- coding: utf-8 -*-
"""Autonomous Shoe Tracking Controller with Pulse-Steering & Shoe Obstacle Exemption."""

import threading
import time


class ShoeTrackerController:
    """Shoe Tracking Controller using YOLO bounding box center, low-pass anti-jitter filter,
    step/pulse-based turn control (0.15s pulse + 0.10s pause), and intelligent shoe obstacle exemption.
    
    Features:
    1. Low-Pass Exponential Anti-Jitter Filter on target center offset (dx).
    2. Timed Step/Pulse Steering (0.15s turn pulse + 0.10s camera pause) to eliminate low-PWM stalling & overshooting.
    3. Shoe Obstacle Exemption: When the shoe is centered in front (abs(dx) <= 100px), ultrasonic distance <= 65cm
       is recognised as the target shoe itself, so obstacle evasion is bypassed and the robot drives straight into the shoe!
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
    ):
        self.lock = threading.Lock()
        self.target_center_x = target_center_x
        self.deadband_px = deadband_px
        self.alpha = smoothing_alpha
        self.obstacle_dist_cm = obstacle_dist_cm
        self.stop_dist_cm = stop_dist_cm
        self.full_shoe_height_ratio = full_shoe_height_ratio
        self.pulse_duration_sec = pulse_duration_sec
        self.pulse_pause_sec = pulse_pause_sec

        self.smoothed_dx = 0.0
        self.has_smoothed = False
        self.evading_obstacle = False
        self.evade_direction = "A"
        self.evade_start_at = 0.0

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
            self.evading_obstacle = False
            self.pulse_state = "IDLE"
            self.pulse_cmd = (0, 0)
            self.pulse_until = 0.0
            self.pause_until = 0.0
            self.reason = "Shoe tracker reset"

    def tick(self, target, distance_cm):
        """核心追蹤週期函數：傳入 YOLO 目標與即時超聲波距離。"""
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
            shoe_centered = abs(dx) <= 100.0  # 鞋子在視角正前方範圍內

            # -------------------------------------------------------------
            # 2. 抵達鞋子特判 (撞到鞋子 / 畫面滿屏鞋子 / 超聲波 <= 15cm)
            # -------------------------------------------------------------
            is_at_shoe = (
                (valid_dist and distance_cm <= self.stop_dist_cm)
                or height_ratio >= self.full_shoe_height_ratio
            )
            if is_at_shoe:
                dist_str = f"{distance_cm:.1f}cm" if valid_dist else "N/A"
                self.reason = f"🎯 Arrived at shoe! (Distance: {dist_str}, Height: {height_ratio*100:.1f}%)"
                return (0, 0), self.reason

            # -------------------------------------------------------------
            # 3. 智能超聲波自動避障 (鞋子居中時免除避障，避免把鞋子當障礙物)
            # -------------------------------------------------------------
            # 只有當「鞋子不在視角正前方 (dx > 100px)」且「前方距離 <= 65cm」時才觸發避障！
            is_side_obstacle = valid_dist and (distance_cm <= self.obstacle_dist_cm) and not shoe_centered

            if is_side_obstacle or self.evading_obstacle:
                if not self.evading_obstacle:
                    self.evading_obstacle = True
                    self.evade_direction = "A" if dx < 0 else "D"
                    self.evade_start_at = now

                path_cleared = (
                    (not valid_dist or distance_cm >= 70.0)
                    and (now - self.evade_start_at >= 0.4)
                )

                if path_cleared:
                    self.evading_obstacle = False
                else:
                    cmd = (-160, 165) if self.evade_direction == "A" else (175, -175)
                    dir_str = "Spin Left (-160, 165)" if self.evade_direction == "A" else "Spin Right (175, -175)"
                    self.reason = f"🚨 Side Obstacle ({distance_cm:.1f}cm <= 65cm) -> Evading: {dir_str}"
                    return cmd, self.reason

            # -------------------------------------------------------------
            # 4. 步進式小幅度脈衝轉向控制 (Step / Pulse Steering Control)
            # -------------------------------------------------------------
            # A) 處理步進脈衝中的動作 (PULSING)
            if self.pulse_state == "PULSING":
                if now < self.pulse_until:
                    return self.pulse_cmd, self.reason
                else:
                    # 脈衝結束，進入簡短停頓讓相機與視覺讀取新幀 (PAUSING)
                    self.pulse_state = "PAUSING"
                    self.pause_until = now + self.pulse_pause_sec
                    return (0, 0), "🎯 Tracking: Steering pulse pause (reading frame)"

            # B) 處理步進脈衝間隔停頓 (PAUSING)
            if self.pulse_state == "PAUSING":
                if now < self.pause_until:
                    return (0, 0), "🎯 Tracking: Steering pulse pause (reading frame)"
                else:
                    self.pulse_state = "IDLE"

            # C) 精確對準中心 (±30px 死區) -> 全速直向前進
            if abs(dx) <= self.deadband_px:
                cmd = (200, 200)
                self.reason = f"🎯 Tracking: Centered (dx={dx:.1f}) -> Driving Forward"
                return cmd, self.reason

            # D) 觸發新的小幅度步進轉向脈衝 (0.15s 強力轉向 + 0.10s 觀察)
            if dx < -90.0:
                # 大幅偏左 -> 0.15s 原地左轉步進
                cmd = (-160, 165)
                act_name = "Step Spin Left (-160, 165)"
            elif dx < -self.deadband_px:
                # 輕微偏左 -> 0.18s 弧線左前步進
                cmd = (90, 240)
                act_name = "Step Curve Left (90, 240)"
            elif dx > 90.0:
                # 大幅偏右 -> 0.15s 原地右轉步進
                cmd = (175, -175)
                act_name = "Step Spin Right (175, -175)"
            else:
                # 輕微偏右 -> 0.18s 弧線右前步進
                cmd = (240, 90)
                act_name = "Step Curve Right (240, 90)"

            self.pulse_state = "PULSING"
            self.pulse_cmd = cmd
            self.pulse_until = now + (0.18 if abs(dx) <= 90 else self.pulse_duration_sec)
            self.reason = f"🎯 Tracking: {act_name} (dx={dx:.1f})"

            return self.pulse_cmd, self.reason
