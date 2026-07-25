# -*- coding: utf-8 -*-
"""Autonomous Pet-like Free Wandering and Ultrasonic Obstacle Avoidance Controller."""

import random
import threading
import time


class PetWanderController:
    """Pet-like free wandering algorithm with real-time ultrasonic obstacle avoidance.
    
    Rules:
    1. Randomly executes non-backward forward actions (W, WA, WD, A, D) and short pauses (B).
    2. Pause duration is kept short (0.4 ~ 0.9s) so it moves around like a pet.
    3. Excludes backward commands (S, SA, SD).
    4. Automatically avoids obstacles at 50cm using ONLY left (A) / right (D) spins (no combination keys).
    """

    def __init__(
        self,
        forward_pwm=200,
        pivot_pwm=120,
        inner_pwm=100,
        obstacle_dist_cm=50.0,
        clear_dist_cm=55.0,
    ):
        self.lock = threading.Lock()
        self.forward_pwm = forward_pwm
        self.pivot_pwm = pivot_pwm
        self.inner_pwm = inner_pwm
        self.obstacle_dist_cm = obstacle_dist_cm
        self.clear_dist_cm = clear_dist_cm

        self.mode = "WANDERING"  # "WANDERING" or "AVOID_OBSTACLE"
        self.current_cmd = (0, 0)
        self.current_action = "B"
        self.action_until = 0.0
        self.avoid_direction = "A"  # 'A' (left spin) or 'D' (right spin)
        self.avoid_start_time = 0.0
        self.reason = "Pet wander initialized"

        # 定義可選的非後退運動動作及其隨機持續時間 (秒) 與 PWM (左, 右)
        self.actions = {
            "W": {
                "pwm": (self.forward_pwm, self.forward_pwm),  # (200, 200)
                "duration": (1.5, 3.5),
                "weight": 40,
                "name": "Forward (W)",
            },
            "WA": {
                "pwm": (self.inner_pwm, self.forward_pwm),    # (100, 200)
                "duration": (1.0, 2.5),
                "weight": 20,
                "name": "Forward-Left (WA)",
            },
            "WD": {
                "pwm": (self.forward_pwm, self.inner_pwm),    # (200, 100)
                "duration": (1.0, 2.5),
                "weight": 20,
                "name": "Forward-Right (WD)",
            },
            "A": {
                "pwm": (-self.pivot_pwm, self.pivot_pwm),     # (-120, 120)
                "duration": (0.5, 1.2),
                "weight": 8,
                "name": "Turn-Left (A)",
            },
            "D": {
                "pwm": (self.pivot_pwm, -self.pivot_pwm),     # (120, -120)
                "duration": (0.5, 1.2),
                "weight": 8,
                "name": "Turn-Right (D)",
            },
            "B": {
                "pwm": (0, 0),
                "duration": (0.4, 0.9),  # 停頓時間短，符合寵物活潑移動
                "weight": 4,
                "name": "Short Pause (B)",
            },
        }

    def _pick_next_action(self, now):
        """隨機抽取下一個運動動作與持續時間。"""
        action_keys = list(self.actions.keys())
        weights = [self.actions[k]["weight"] for k in action_keys]

        # 避免連續兩次長時間停頓
        if self.current_action == "B":
            action_keys.remove("B")
            weights = [self.actions[k]["weight"] for k in action_keys]

        chosen = random.choices(action_keys, weights=weights, k=1)[0]
        act_info = self.actions[chosen]
        dur = random.uniform(*act_info["duration"])

        self.current_action = chosen
        self.current_cmd = act_info["pwm"]
        self.action_until = now + dur
        self.reason = f"Pet wandering: {act_info['name']} for {dur:.1f}s"

    def reset(self):
        """重置漫遊控制器。"""
        with self.lock:
            self.mode = "WANDERING"
            self.current_cmd = (0, 0)
            self.current_action = "B"
            self.action_until = 0.0
            self.reason = "Pet wander reset"

    def tick(self, distance_cm):
        """核心週期函數：傳入即時超聲波距離，返回馬達速度指令 (pwml, pwmr) 與原因。"""
        now = time.monotonic()
        with self.lock:
            is_obstacle = (
                distance_cm is not None
                and distance_cm > 0.0
                and distance_cm <= self.obstacle_dist_cm
            )

            # -------------------------------------------------------------
            # 1. 50cm 超聲波自動避障模式 (單純左轉 A 或右轉 D 避障，不用組合鍵)
            # -------------------------------------------------------------
            if is_obstacle or self.mode == "AVOID_OBSTACLE":
                if self.mode != "AVOID_OBSTACLE":
                    # 剛剛觸發 50cm 避障：隨機選擇原地左轉 (A: -120, 120) 或右轉 (D: 120, -120)
                    self.mode = "AVOID_OBSTACLE"
                    self.avoid_direction = random.choice(["A", "D"])
                    self.avoid_start_time = now

                # 判斷道路是否恢復清空 (超聲波 > 55cm 且轉動超過最小時間 0.4s)
                path_cleared = (
                    distance_cm is not None
                    and distance_cm >= self.clear_dist_cm
                    and (now - self.avoid_start_time >= 0.4)
                )

                if path_cleared:
                    # 障礙物已清除，回歸隨機漫遊
                    self.mode = "WANDERING"
                    self._pick_next_action(now)
                else:
                    # 原地旋轉避障 (僅使用 A: -120, 120 或 D: 120, -120)
                    if self.avoid_direction == "A":
                        self.current_cmd = (-self.pivot_pwm, self.pivot_pwm)
                        dir_str = "Spin Left (A: -120, 120)"
                    else:
                        self.current_cmd = (self.pivot_pwm, -self.pivot_pwm)
                        dir_str = "Spin Right (D: 120, -120)"
                    dist_str = f"{distance_cm:.1f}cm" if distance_cm is not None else "N/A"
                    self.reason = f"🚨 Obstacle ({dist_str} <= {self.obstacle_dist_cm}cm) -> Evading: {dir_str}"
                    return self.current_cmd, self.reason

            # -------------------------------------------------------------
            # 2. 寵物自由漫遊模式 (WANDERING)
            # -------------------------------------------------------------
            if now >= self.action_until:
                self._pick_next_action(now)

            return self.current_cmd, self.reason

    @property
    def command(self):
        return self.current_cmd
