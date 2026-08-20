"""Forward validated drive commands to the Raspberry Pi control agent.

The dashboard process never writes GPIO or PWM itself.  It sends a small,
explicit HTTP command to the process that owns the rover actuators.  This
keeps browser/API failures outside the motor-control loop and lets the rover
agent enforce its own heartbeat fail-safe.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from enum import Enum


class DriveCommand(str, Enum):
    START = "START"
    STOP = "STOP"
    HEARTBEAT = "HEARTBEAT"


class ControlUnavailableError(ConnectionError):
    pass


class ControlCommandError(RuntimeError):
    pass


class RoverControlService:
    def __init__(
        self,
        control_url: str | None,
        timeout_s: float = 2.0,
        token: str | None = None,
    ) -> None:
        self._control_url = control_url.strip() if control_url else None
        self._timeout_s = timeout_s
        self._token = token

    @property
    def configured(self) -> bool:
        return bool(self._control_url)

    def send(self, command: DriveCommand, target_speed_mps: float | None = None) -> dict:
        if not self._control_url:
            raise ControlUnavailableError(
                "차량 제어 API가 설정되지 않았습니다. "
                "DASHBOARD_ROVER_CONTROL_URL을 설정하세요."
            )

        payload: dict[str, object] = {
            "command_id": str(uuid.uuid4()),
            "command": command.value,
            "sent_at_ms": int(time.time() * 1000),
        }
        if command is DriveCommand.START:
            if target_speed_mps is None:
                raise ControlCommandError("START 명령에는 목표 속도가 필요합니다.")
            payload["target_speed_mps"] = target_speed_mps

        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self._control_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ControlCommandError(
                f"차량이 명령을 거부했습니다 (HTTP {exc.code}): {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ControlUnavailableError(f"차량 제어 API에 연결할 수 없습니다: {exc}") from exc

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControlCommandError("차량 제어 API가 올바른 JSON을 반환하지 않았습니다.") from exc
        if not isinstance(result, dict) or result.get("accepted") is not True:
            reason = result.get("reason", "거부 사유 없음") if isinstance(result, dict) else "잘못된 응답"
            raise ControlCommandError(f"차량이 명령을 승인하지 않았습니다: {reason}")

        return {
            "accepted": True,
            "command": command.value,
            "command_id": payload["command_id"],
            "target_speed_mps": payload.get("target_speed_mps"),
            "rover": result,
        }
