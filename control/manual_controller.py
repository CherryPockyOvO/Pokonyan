# -*- coding: utf-8 -*-
"""Manual WASD and Diagonal (WA, WD, SA, SD) controller module for web remote control."""

import threading
import time


class ManualController:
    """Manual control handling WASD and combination key commands with fine-tuned asymmetric motor compensation."""

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

            # 組合鍵判斷 (微調：WA 65, 200; SA -85, -150)
            if cmd in ("WD", "DW"):
                self.command = (200, 70)          # 右前弧線 (左輪200, 右輪70)
                self.reason = "manual forward-right (WD: 200, 70)"
            elif cmd in ("WA", "AW"):
                self.command = (65, 200)          # 左前弧線 (降至65：加大左轉弧度，防止走直線)
                self.reason = "manual forward-left (WA: 65, 200)"
            elif cmd in ("SD", "DS"):
                self.command = (-150, -60)        # 右後弧線 (左輪-150, 右輪-60)
                self.reason = "manual backward-right (SD: -150, -60)"
            elif cmd in ("SA", "AS"):
                self.command = (-85, -150)        # 左後弧線 (升至-85：提供足夠倒車動力，防止原地卡死)
                self.reason = "manual backward-left (SA: -85, -150)"
            # 單鍵判斷
            elif cmd in ("W", "FORWARD"):
                self.command = (200, 200)         # 直向前進 200, 200
                self.reason = "manual forward (W: 200, 200)"
            elif cmd in ("S", "BACKWARD"):
                self.command = (-150, -150)       # 直線後退 -150, -150
                self.reason = "manual backward (S: -150, -150)"
            elif cmd in ("A", "LEFT"):
                self.command = (-140, 140)        # 左拐 -140, 140
                self.reason = "manual turn left (A: -140, 140)"
            elif cmd in ("D", "RIGHT"):
                self.command = (150, -150)        # 右拐 150, -150
                self.reason = "manual turn right (D: 150, -150)"
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
