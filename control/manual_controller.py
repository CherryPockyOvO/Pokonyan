# -*- coding: utf-8 -*-
"""Manual WASD and Diagonal (WA, WD, SA, SD) controller module for web remote control."""

import threading
import time


class ManualController:
    """Manual control handling WASD and combination key commands with fine-tuned asymmetric motor compensation (240 max speed)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.command = (0, 0)
        self.reason = "manual standby"
        self.last_cmd_at = time.monotonic()

    def handle_command(self, cmd):
        cmd = str(cmd).upper().strip()
        with self.lock:
            now = time.monotonic()
            self.last_cmd_at = now

            # 組合鍵判斷 (240 前進 / 200 後退動態等級)
            if cmd in ("WD", "DW"):
                self.command = (240, 80)          # 右前弧線 (左輪240, 右輪80)
                self.reason = "manual forward-right (WD: 240, 80)"
            elif cmd in ("WA", "AW"):
                self.command = (75, 240)          # 左前弧線 (左輪75, 右輪240)
                self.reason = "manual forward-left (WA: 75, 240)"
            elif cmd in ("SD", "DS"):
                self.command = (-200, -80)        # 右後弧線 (左輪-200, 右輪-80)
                self.reason = "manual backward-right (SD: -200, -80)"
            elif cmd in ("SA", "AS"):
                self.command = (-90, -200)        # 左後弧線 (降至-90：加大左後倒車弧度，防止直行)
                self.reason = "manual backward-left (SA: -90, -200)"
            # 單鍵判斷
            elif cmd in ("W", "FORWARD"):
                self.command = (240, 240)         # 直向前進 (240, 240)
                self.reason = "manual forward (W: 240, 240)"
            elif cmd in ("S", "BACKWARD"):
                self.command = (-200, -200)       # 直線後退 (-200, -200)
                self.reason = "manual backward (S: -200, -200)"
            elif cmd in ("A", "LEFT"):
                self.command = (-160, 165)        # 左拐 (-160, 165)
                self.reason = "manual turn left (A: -160, 165)"
            elif cmd in ("D", "RIGHT"):
                self.command = (175, -175)        # 右拐 (175, -175)
                self.reason = "manual turn right (D: 175, -175)"
            elif cmd in ("B", "STOP", "BRAKE"):
                self.command = (0, 0)
                self.reason = "manual brake (B: 0, 0)"
            return True

    def emergency_stop(self):
        with self.lock:
            self.command = (0, 0)
            self.reason = "manual emergency stop"

    def tick(self, motor_status):
        with self.lock:
            if not motor_status["ready"]:
                self.reason = "Arduino telemetry unavailable (MANUAL)"
                return (0, 0)
            return self.command

    def get_status(self):
        with self.lock:
            return {
                "reason": self.reason,
                "command_left": self.command[0],
                "command_right": self.command[1],
            }
