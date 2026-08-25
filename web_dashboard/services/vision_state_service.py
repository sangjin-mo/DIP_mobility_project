"""Send rover state transitions to the separate webcam Raspberry Pi.

Repeated status polling and HEARTBEAT responses are deliberately deduplicated.
The first observed state establishes a local baseline without transmission;
only a later, different state produces an HTTP request.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid


class VisionStateService:
    ALLOWED_STATES = {"RUNNING", "STOPPED", "EMERGENCY"}

    def __init__(
        self,
        receiver_url: str | None,
        *,
        capture_url: str | None = None,
        token: str | None = None,
        timeout_s: float = 2.0,
    ) -> None:
        self._receiver_url = receiver_url.strip() if receiver_url else None
        self._capture_url = capture_url.rstrip("/") if capture_url else None
        self._token = token
        self._timeout_s = timeout_s
        self._last_state: str | None = None
        self._last_result: dict | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._receiver_url)

    @property
    def capture_configured(self) -> bool:
        return bool(self._capture_url)

    @property
    def last_result(self) -> dict | None:
        with self._lock:
            return dict(self._last_result) if self._last_result else None

    def observe(self, rover_status: dict) -> dict:
        """Forward one event only when the observed rover state changes."""
        state = rover_status.get("state")
        if state not in self.ALLOWED_STATES:
            return {"changed": False, "delivered": False, "reason": "invalid_state"}

        with self._lock:
            if self._last_state is None:
                self._last_state = state
                result = {
                    "changed": False,
                    "delivered": False,
                    "state": state,
                    "reason": "baseline_initialized",
                }
                self._last_result = result
                return dict(result)
            if state == self._last_state:
                return {
                    "changed": False,
                    "delivered": False,
                    "state": state,
                    "reason": "unchanged",
                }

            previous_state = self._last_state
            self._last_state = state
            payload = {
                "event_id": str(uuid.uuid4()),
                "state": state,
                "previous_state": previous_state,
                "target_speed_mps": rover_status.get("target_speed_mps"),
                "changed_at_ms": int(time.time() * 1000),
                "source": "web_dashboard",
            }

            if not self._receiver_url:
                result = {
                    "changed": True,
                    "delivered": False,
                    "state": state,
                    "reason": "not_configured",
                }
                self._last_result = result
                return dict(result)

            request = urllib.request.Request(
                self._receiver_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                    raw = response.read().decode("utf-8")
                response_payload = json.loads(raw)
                if not isinstance(response_payload, dict) or response_payload.get("accepted") is not True:
                    raise ValueError("receiver did not accept state")
                result = {"changed": True, "delivered": True, "state": state}
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                result = {
                    "changed": True,
                    "delivered": False,
                    "state": state,
                    "reason": str(exc),
                }
            self._last_result = result
            return dict(result)

    def capture_now(self) -> dict:
        return self._request_capture_api("/capture-now", method="POST")

    def start_capture(self, patrol_id: str | None = None) -> dict:
        """Arm patrol capture on the webcam Pi. Called on START.

        Pushed at the instant the button is pressed rather than discovered by
        the Pi polling the rover's status. The old arrangement gated capture
        on `GET /api/control/status` reporting RUNNING, which tracks "the
        drive process is alive" rather than "a patrol is underway" -- so
        capture ran during stretches with no patrol at all and could be off
        during one. It also cost up to one poll interval of lag at each end.
        """
        result = self._request_capture_api(
            "/capture/start", method="POST", payload={"patrol_id": patrol_id}
        )
        return {"armed": bool(result.get("armed")), "patrol_id": result.get("patrol_id")}

    def stop_capture(self) -> dict:
        """Disarm patrol capture on the webcam Pi. Called on STOP."""
        result = self._request_capture_api("/capture/stop", method="POST")
        return {"armed": bool(result.get("armed")), "was_armed": bool(result.get("was_armed"))}

    def capture_state(self) -> dict:
        """Whether the Pi currently considers itself capturing."""
        result = self._request_capture_api("/capture/state", method="GET")
        return {"armed": bool(result.get("armed")), "patrol_id": result.get("patrol_id")}

    def capture_status(self) -> dict:
        result = self._request_capture_api("/interval", method="GET")
        interval_s = self._required_number(result, "interval_sec")
        min_interval_s = self._required_number(result, "min_interval_sec")
        return {
            "interval_s": interval_s,
            "min_interval_s": min_interval_s,
            "max_interval_s": 10.0,
        }

    def set_capture_interval(self, interval_s: float) -> dict:
        if not 0.2 <= interval_s <= 10.0:
            raise ValueError("촬영 주기는 0.2초 이상 10초 이하여야 합니다.")
        result = self._request_capture_api(
            "/set-interval",
            method="POST",
            payload={"interval_sec": interval_s},
        )
        applied_interval_s = self._required_number(result, "interval_sec")
        return {
            "interval_s": applied_interval_s,
            "requested_s": result.get("requested_sec", interval_s),
            "clamped": result.get("clamped", False),
        }

    @staticmethod
    def _required_number(result: dict, key: str) -> float:
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"비전 Pi 촬영 API 응답에 {key} 값이 없습니다.")
        return float(value)

    def _request_capture_api(
        self,
        path: str,
        *,
        method: str,
        payload: dict | None = None,
    ) -> dict:
        if not self._capture_url:
            raise ConnectionError("비전 웹캠 Pi 촬영 API 주소가 설정되지 않았습니다.")
        url = f"{self._capture_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"비전 Pi가 촬영 요청을 거부했습니다: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"비전 Pi 촬영 API에 연결할 수 없습니다: {exc}") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("비전 Pi 촬영 API 응답이 올바른 JSON이 아닙니다.") from exc
        if not isinstance(result, dict) or result.get("status") == "error":
            reason = result.get("reason", "잘못된 응답") if isinstance(result, dict) else "잘못된 응답"
            raise RuntimeError(f"비전 Pi 촬영 요청 실패: {reason}")
        return result

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers
