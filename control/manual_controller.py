# -*- coding: utf-8 -*-
"""Manual WASD and Diagonal (WA, WD, SA, SD) controller module for web remote control."""

import threading
import time


class ManualController:
    """Manual control handling WASD and combination key commands."""

    def __init__(self, forward_pwm=250, pivot_pwm=200, inner_pwm=100):
        self.lock = threading.Lock()
        self.command = (0, 0)
        self.reason = "manual standby"
        self.forward_pwm = forward_pwm
        self.pivot_pwm = pivot_pwm
        self.inner_pwm = inner_pwm
        self.last_cmd_at = time.monotonic()

    def handle_command(self, cmd):
        cmd = str(cmd).upper().strip()
        with self.lock:
            now = time.monotonic()
            self.last_cmd_at = now
            pwm = self.forward_pwm     # 250
            pivot = self.pivot_pwm   # 200
            inner = self.inner_pwm   # 150

            # 組合鍵判斷
            if cmd in ("WD", "DW"):
                self.command = (pwm, inner)
                self.reason = "manual forward-right (WD)"
            elif cmd in ("WA", "AW"):
                self.command = (inner, pwm)
                self.reason = "manual forward-left (WA)"
            elif cmd in ("SD", "DS"):
                self.command = (-pwm, -inner)
                self.reason = "manual backward-right (SD)"
            elif cmd in ("SA", "AS"):
                self.command = (-inner, -pwm)
                self.reason = "manual backward-left (SA)"
            # 單鍵判斷
            elif cmd in ("W", "FORWARD"):
                self.command = (pwm, pwm)
                self.reason = "manual forward (W)"
            elif cmd in ("S", "BACKWARD"):
                self.command = (-pwm, -pwm)
                self.reason = "manual backward (S)"
            elif cmd in ("A", "LEFT"):
                self.command = (-pivot, pivot)
                self.reason = "manual turn left (A)"
            elif cmd in ("D", "RIGHT"):
                self.command = (pivot, -pivot)
                self.reason = "manual turn right (D)"
            elif cmd in ("B", "STOP", "BRAKE"):
                self.command = (0, 0)
                self.reason = "manual brake (B)"
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
