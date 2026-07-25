# -*- coding: utf-8 -*-
"""Autonomous Shoe Tracking Controller with Bounding Box Center Anti-Jitter Smoothing & Obstacle Avoidance."""

import random
import threading
import time


class ShoeTrackerController:
    """Shoe Tracking Controller using YOLO bounding box center, low-pass anti-jitter filter,
    gentle steering angle limits, and ultrasonic obstacle avoidance.
    
    Features:
    1. Exponential moving average (Low-Pass Filter) on target center offset (dx) to eliminate steering jitter.
    2. Reduced turn intensity (gentle curved speeds) to prevent overshooting caused by camera latency.
    3. Ultrasonic obstacle avoidance during tracking:
       - Avoids walls/obstacles if distance <= 65cm.
       - Exception: Stops cleanly if the obstacle IS the shoe itself (distance <= 15cm or shoe takes over 65% of screen height).
    """

    def __init__(
        self,
        target_center_x=320.0,
        deadband_px=30.0,
        smoothing_alpha=0.3,
        obstacle_dist_cm=65.0,
        stop_dist_cm=15.0,
        full_shoe_height_ratio=0.65,
    ):
        self.lock = threading.Lock()
        self.target_center_x = target_center_x
        self.deadband_px = deadband_px
        self.alpha = smoothing_alpha
        self.obstacle_dist_cm = obstacle_dist_cm
        self.stop_dist_cm = stop_dist_cm
        self.full_shoe_height_ratio = full_shoe_height_ratio

        self.smoothed_dx = 0.0
        self.has_smoothed = False
        self.evading_obstacle = False
        self.evade_direction = "A"
        self.evade_start_at = 0.0
        self.reason = "Shoe tracker initialized"

    def reset(self):
        """重置追蹤器平滑狀態與避障狀態。"""
        with self.lock:
            self.smoothed_dx = 0.0
            self.has_smoothed = False
            self.evading_obstacle = False
            self.reason = "Shoe tracker reset"

    def tick(self, target, distance_cm):
        """核心追蹤週期函數：傳入 YOLO 目標 target 與即時超聲波距離 distance_cm。"""
        now = time.monotonic()
        with self.lock:
            if target is None:
                self.reset()
                return (0, 0), "No target for tracking"

            raw_dx = target["center_x"] - self.target_center_x
            height_ratio = target.get("height_ratio", 0.0)

            # -------------------------------------------------------------
            # 1. 低通指數平滑消抖算法 (Low-Pass Exponential Filter for Anti-Jitter)
            # -------------------------------------------------------------
            if not self.has_smoothed:
                self.smoothed_dx = raw_dx
                self.has_smoothed = True
            else:
                self.smoothed_dx = self.alpha * raw_dx + (1.0 - self.alpha) * self.smoothed_dx

            dx = self.smoothed_dx
            valid_dist = distance_cm is not None and distance_cm > 0.0

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
            # 3. 追蹤過程中的超聲波自動避障 (非鞋子障礙物避障)
            # -------------------------------------------------------------
            is_obstacle = valid_dist and (distance_cm <= self.obstacle_dist_cm)

            if is_obstacle or self.evading_obstacle:
                if not self.evading_obstacle:
                    self.evading_obstacle = True
                    # 優先根據偏離方向避障：若鞋子在左邊則向左轉避開右側障礙，反之亦然
                    self.evade_direction = "A" if dx < 0 else "D"
                    self.evade_start_at = now

                path_cleared = (
                    (not valid_dist or distance_cm >= 70.0)
                    and (now - self.evade_start_at >= 0.4)
                )

                if path_cleared:
                    self.evading_obstacle = False
                else:
                    if self.evade_direction == "A":
                        cmd = (-140, 140)
                        dir_str = "Spin Left (A: -140, 140)"
                    else:
                        cmd = (150, -150)
                        dir_str = "Spin Right (D: 150, -150)"
                    self.reason = f"🚨 Obstacle while tracking ({distance_cm:.1f}cm <= 65cm) -> Evading: {dir_str}"
                    return cmd, self.reason

            # -------------------------------------------------------------
            # 4. 溫和減小轉向幅度 (防止延遲導致過度轉向與擺動)
            # -------------------------------------------------------------
            if dx < -90.0:
                # 較大偏左 -> 溫和原地左轉 (-90, 140) 減小轉動震盪
                cmd = (-90, 140)
                self.reason = f"🎯 Tracking: Target Far Left (smoothed dx={dx:.1f}) -> Soft Spin Left"
            elif dx < -self.deadband_px:
                # 輕微偏左 -> 平滑弧線左前 (120, 220)
                cmd = (120, 220)
                self.reason = f"🎯 Tracking: Target Left (smoothed dx={dx:.1f}) -> Gentle Curve Left"
            elif dx > 90.0:
                # 較大偏右 -> 溫和原地右轉 (140, -90) 減小轉動震盪
                cmd = (140, -90)
                self.reason = f"🎯 Tracking: Target Far Right (smoothed dx={dx:.1f}) -> Soft Spin Right"
            elif dx > self.deadband_px:
                # 輕微偏右 -> 平滑弧線右前 (220, 120)
                cmd = (220, 120)
                self.reason = f"🎯 Tracking: Target Right (smoothed dx={dx:.1f}) -> Gentle Curve Right"
            else:
                # 精確對準中心 (±30px 死區) -> 全速直向前進 (200, 200)
                cmd = (200, 200)
                self.reason = f"🎯 Tracking: Centered (smoothed dx={dx:.1f}) -> Drive Forward"

            return cmd, self.reason
