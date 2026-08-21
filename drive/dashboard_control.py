"""Dashboard command input for the DonkeyCar vehicle loop.

This module is intentionally independent of DonkeyCar so its command and
watchdog behaviour can be tested on a PC. ``DashboardControlPart`` is added as
a threaded DonkeyCar part by ``manage.py`` and becomes the sole source of
user-mode steering/throttle while dashboard control is enabled.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar


class CommandRejected(ValueError):
    pass


class DashboardControlPart:
    VALID_COMMANDS: ClassVar[set[str]] = {
        "START",
        "STOP",
        "HEARTBEAT",
    }

    def __init__(
        self,
        host: str,
        port: int,
        token: str | None,
        heartbeat_timeout_s: float,
        max_speed_mps: float,
        max_throttle: float,
        straight_steering: float = 0.0,
        use_pilot_steering: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if heartbeat_timeout_s <= 0:
            raise ValueError("heartbeat_timeout_s must be positive")
        if max_speed_mps <= 0 or not 0 < max_throttle <= 1:
            raise ValueError("max speed/throttle must be positive and throttle <= 1")
        if not -1 <= straight_steering <= 1:
            raise ValueError("straight_steering must be between -1 and 1")

        self._host = host
        self._port = port
        self._token = token
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._max_speed_mps = max_speed_mps
        self._max_throttle = max_throttle
        self._straight_steering = straight_steering
        self._use_pilot_steering = use_pilot_steering
        self._clock = clock
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

        self._state = "STOPPED"
        self._target_speed_mps = 0.0
        self._last_heartbeat = self._clock()
        self._last_command_id: str | None = None

    def update(self) -> None:
        """DonkeyCar threaded-part loop: own the command HTTP server."""
        server = ThreadingHTTPServer((self._host, self._port), _CommandHandler)
        server.control_part = self  # type: ignore[attr-defined]
        self._server = server
        print(f"Dashboard control API on http://{self._host}:{self._port}/api/control")
        server.serve_forever(poll_interval=0.2)

    def run_threaded(
        self,
        _user_angle: float | None,
        _user_throttle: float | None,
        _user_mode: str | None,
    ) -> tuple[float, float, str]:
        """Return safe drive commands for the current dashboard state.

        While RUNNING, throttle is always the dashboard's own calibrated,
        speed-capped value regardless of steering source. Steering is either
        a fixed straight value, or handed to the trained pilot model via
        DonkeyCar's 'local_angle' mode (requires manage.py to be started with
        --model and DASHBOARD_USE_PILOT_STEERING enabled; see README).
        """
        with self._lock:
            self._apply_watchdog_locked()
            if self._state != "RUNNING":
                return 0.0, 0.0, "user"
            throttle = (self._target_speed_mps / self._max_speed_mps) * self._max_throttle
            throttle = min(max(throttle, 0.0), self._max_throttle)
            mode = "local_angle" if self._use_pilot_steering else "user"
            return self._straight_steering, throttle, mode

    def shutdown(self) -> None:
        """DonkeyCar lifecycle hook; stopping the part also means stopping motors."""
        with self._lock:
            self._state = "STOPPED"
            self._target_speed_mps = 0.0
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def authenticate(self, authorization: str | None) -> bool:
        if not self._token:
            return True
        expected = f"Bearer {self._token}"
        return bool(authorization and hmac.compare_digest(authorization, expected))

    def apply_command(self, payload: dict) -> dict:
        command = payload.get("command")
        command_id = payload.get("command_id")
        if command not in self.VALID_COMMANDS:
            raise CommandRejected("unknown command")
        if not isinstance(command_id, str) or not command_id:
            raise CommandRejected("command_id is required")

        with self._lock:
            now = self._clock()
            if command == "START":
                speed = payload.get("target_speed_mps")
                if not isinstance(speed, (int, float)) or isinstance(speed, bool):
                    raise CommandRejected("target_speed_mps is required")
                if not 0 < float(speed) <= self._max_speed_mps:
                    raise CommandRejected(
                        f"target_speed_mps must be greater than 0 and at most "
                        f"{self._max_speed_mps:.3f}"
                    )
                self._state = "RUNNING"
                self._target_speed_mps = float(speed)
                self._last_heartbeat = now
            elif command == "STOP":
                self._state = "STOPPED"
                self._target_speed_mps = 0.0
                self._last_heartbeat = now
            elif command == "HEARTBEAT" and self._state == "RUNNING":
                self._last_heartbeat = now

            self._last_command_id = command_id
            return self._snapshot_locked(command_id)

    def snapshot(self) -> dict:
        with self._lock:
            self._apply_watchdog_locked()
            return self._snapshot_locked(self._last_command_id)

    def _apply_watchdog_locked(self) -> None:
        if (
            self._state == "RUNNING"
            and self._clock() - self._last_heartbeat > self._heartbeat_timeout_s
        ):
            self._state = "STOPPED"
            self._target_speed_mps = 0.0

    def _snapshot_locked(self, command_id: str | None) -> dict:
        return {
            "accepted": True,
            "command_id": command_id,
            "state": self._state,
            "target_speed_mps": self._target_speed_mps,
        }


class _CommandHandler(BaseHTTPRequestHandler):
    server_version = "PiRacerDashboardControl/1.0"

    @property
    def control(self) -> DashboardControlPart:
        return self.server.control_part  # type: ignore[attr-defined, no-any-return]

    def do_GET(self) -> None:
        if self.path in {"/", "/WebInterface.html"}:
            self._send_interface()
            return
        if self.path == "/api/status":
            self._send_json(200, self.control.snapshot())
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/control":
            self._send_json(404, {"detail": "not found"})
            return
        if not self.control.authenticate(self.headers.get("Authorization")):
            self._send_json(401, {"accepted": False, "reason": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise CommandRejected("request body must be an object")
            result = self.control.apply_command(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, CommandRejected, ValueError) as exc:
            self._send_json(400, {"accepted": False, "reason": str(exc)})
            return
        self._send_json(200, result)

    def log_message(self, format: str, *args) -> None:
        print("dashboard-control: " + (format % args))

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_interface(self) -> None:
        path = Path(__file__).with_name("WebInterface.html")
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(500, {"detail": "WebInterface.html not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
