# 2호기 -> PC web_dashboard 클라이언트
#
# 2026-08-22: 정지(표지판·주먹)도 이 클라이언트로 통일함 — "1호기 직접(PC 안 거침)"
# 방식(vehicle_control_client.py, 이제 미사용)에서 "PC 대시보드의 정지 버튼과 동일한
# 경로"로 되돌림. PC가 꺼지면 정지 자체가 안 되는 리스크를 감수하기로 결정
# (../../../mediapipe/design/README.md §3-1-1 참고, 이 변경의 배경·트레이드오프 기록).

from __future__ import annotations

import json
import urllib.error
import urllib.request


class DashboardClientError(RuntimeError):
    pass


def _request(url: str, *, method: str, body: dict | None, timeout_s: float) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DashboardClientError(f"대시보드가 요청을 거부함 (HTTP {exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DashboardClientError(f"대시보드 API에 연결할 수 없음: {exc}") from exc


def get_status(dashboard_url: str, *, timeout_s: float = 2.0) -> dict:
    """현재 state(RUNNING/STOPPED/EMERGENCY)와 target_speed_mps 조회."""
    return _request(f"{dashboard_url}/api/control/status", method="GET", body=None, timeout_s=timeout_s)


def stop(dashboard_url: str, *, timeout_s: float = 2.0) -> dict:
    """정지 — 대시보드의 "■ 정지" 버튼과 완전히 동일한 API 호출 (표지판·주먹 공용)."""
    return _request(f"{dashboard_url}/api/control/stop", method="POST", body=None, timeout_s=timeout_s)


def set_speed(dashboard_url: str, target_speed_mps: float, *, timeout_s: float = 2.0) -> dict:
    """실행 중인 목표 속도를 절대값으로 갱신 (슬라이더를 다시 조작하는 것과 동일한 호출)."""
    if target_speed_mps <= 0:
        raise ValueError("target_speed_mps must be positive")
    body = {"target_speed_mps": target_speed_mps}
    return _request(f"{dashboard_url}/api/control/start", method="POST", body=body, timeout_s=timeout_s)


def send_heartbeat(dashboard_url: str, *, timeout_s: float = 2.0) -> dict:
    """워치독(1.5초)이 제스처 쿨타임(2초)보다 짧아 별도 생존 신호가 필요 (README §3-1-4 문제 4)."""
    return _request(f"{dashboard_url}/api/control/heartbeat", method="POST", body=None, timeout_s=timeout_s)
