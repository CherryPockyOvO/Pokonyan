# -*- coding: utf-8 -*-
"""Automatic state machine controller for audio/vision triggered missions."""

import threading
import time


class AutoController:
    """State machine for automatic shoe-seeking mission."""

    def __init__(self, forward_pwm=250, slow_pwm=150, pivot_pwm=200, slow_distance_cm=15.0):
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
        self.rotate_step_seconds = 0.45
        self.scan_pause_seconds = 2.0
        self.forward_seconds = 10.0

    def _transition(self, state, now, reason):
        if state != self.state:
            print(f"[AutoController] {self.state} -> {state}: {reason}")
            self.state = state
            self.state_since = now
        self.reason = reason

    def trigger(self, event, score):
        if event not in ("alarm", "alarm_clock"):
            return False
        now = time.monotonic()
        with self.lock:
            if self.state not in ("IDLE", "DONE", "FAILED"):
                return False
            self.alert = event
            self.alert_score = score
            self.scan_steps = 0
            self._transition("SEARCH_PAUSE", now, f"initial camera check: {event}")
            return True

    def emergency_stop(self):
        with self.lock:
            self._transition("E_STOP", time.monotonic(), "emergency stop triggered")
            self.command = (0, 0)

    def _stop(self, reason):
        self.command = (0, 0)
        self.reason = reason
        return self.command

    def _forward(self, distance):
        near = distance is not None and distance <= self.slow_distance_cm
        pwm = self.slow_pwm if near else self.forward_pwm
        self.command = (pwm, pwm)
        self.reason = "distance at or below 15 cm" if near else "timed forward run"
        return self.command

    def tick(self, target, motor_status):
        now = time.monotonic()
        with self.lock:
            elapsed = now - self.state_since

            if self.state in ("IDLE", "DONE", "FAILED", "E_STOP"):
                return self._stop(self.reason)

            if not motor_status["ready"]:
                self._transition("FAILED", now, "Arduino telemetry unavailable")
                return self._stop(self.reason)

            distance = motor_status["distance_cm"]

            if self.state == "SEARCH_TURN":
                if target is not None:
                    self._transition("FORWARD", now, "shoe detected")
                    return self._forward(distance)
                if elapsed >= self.rotate_step_seconds:
                    self.scan_steps += 1
                    self._transition("SEARCH_PAUSE", now, "45 degree step complete")
                    return self._stop("camera pause")
                self.command = (-self.pivot_pwm, self.pivot_pwm)
                self.reason = f"scan step {self.scan_steps + 1}"
                return self.command

            if self.state == "SEARCH_PAUSE":
                if target is not None:
                    self._transition("FORWARD", now, "shoe detected")
                    return self._forward(distance)
                if elapsed >= self.scan_pause_seconds:
                    self._transition("SEARCH_TURN", now, "continue scan")
                return self._stop("camera pause")

            if self.state == "FORWARD":
                if elapsed >= self.forward_seconds:
                    self._transition("DONE", now, "timed forward run complete")
                    return self._stop(self.reason)
                return self._forward(distance)

            self._transition("FAILED", now, "unknown state")
            return self._stop(self.reason)

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
