# 2호기 -> 1호기 직접 인증 STOP/START 클라이언트 (PC를 거치지 않음)
#
# 배경: 원래 GPIO 핀 직결(design/README.md 구 버전 §3)로 정지 신호를 보낼 계획이었으나
# 점퍼케이블 연결이 물리적으로 불가능해져 HTTP로 전환함
# (../../../mediapipe/design/README.md §3-1 참고).
#
# PC의 web_dashboard를 거치지 않고 1호기의 제어 에이전트(dashboard_control.py)에
# 직접 요청하는 이유: 정지·재출발은 PC가 죽어 있어도 동작해야 하는 안전 요구사항이기
# 때문 (DIP_mobility_project/drive_ver3/vision_stop_client.py와 동일한 패턴).

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid


class VehicleControlError(RuntimeError):
    pass


def _send_command(control_url: str, token: str, payload: dict, timeout_s: float) -> dict:
    request = urllib.request.Request(
        control_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VehicleControlError(f"1호기가 명령을 거부함 (HTTP {exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VehicleControlError(f"1호기 제어 API에 연결할 수 없음: {exc}") from exc


def send_stop(control_url: str, token: str, *, timeout_s: float = 2.0) -> dict:
    """1호기를 즉시 STOPPED 상태로 전환 (표지판 인식·주먹 제스처 공용)."""
    payload = {
        "command": "STOP",
        "command_id": str(uuid.uuid4()),
        "sent_at_ms": int(time.time() * 1000),
    }
    return _send_command(control_url, token, payload, timeout_s)


def send_start(control_url: str, token: str, target_speed_mps: float, *, timeout_s: float = 2.0) -> dict:
    """1호기를 지정한 목표 속도로 재출발시킴 (정지 해제 시 정지 전 속도 복원용)."""
    if target_speed_mps <= 0:
        raise ValueError("target_speed_mps must be positive")
    payload = {
        "command": "START",
        "command_id": str(uuid.uuid4()),
        "sent_at_ms": int(time.time() * 1000),
        "target_speed_mps": target_speed_mps,
    }
    return _send_command(control_url, token, payload, timeout_s)
