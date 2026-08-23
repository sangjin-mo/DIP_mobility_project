# 제스처 -> 차량 제어 명령 디스패치 상태 머신
# 흐름: gesture_recognizer.recognize() 결과 -> 디바운스 -> 정지/가속/감속 명령
# 참고: design/README.md §3-1, §3-1-2, §3-1-4, §3-2-1, §3-2-3
#
# 정지 상태는 "사유(reason) 집합"으로 관리한다 — 정지 표지판(YOLO)과 주먹 제스처가
# 같은 카메라·같은 1호기를 공유하므로, 둘 중 하나라도 정지를 요구하는 동안은 계속
# 정지 상태를 유지한다.
#
# 단, 자동 재출발(release_stop)은 표지판(STOP_SIGN)에서만 쓴다 — 표지판이 화면에서
# 사라지면 다시 달려야 하는 건 stop_sign 원래 기능이라 그대로 유지. 주먹(FIST)은
# "정지"만 요청하며, 손을 풀어도 자동으로 재출발시키지 않는다 — 재출발은 사람이
# 대시보드에서 직접 하는 별개 동작 (요청 범위 밖). 표지판 검출은 detector.py 쪽
# 디바운스 루프가 request_stop("STOP_SIGN")/release_stop("STOP_SIGN")를 호출해
# 이 클래스에 위임한다.

from __future__ import annotations

import threading
import time

import gesture_config as cfg


def _clamp_speed(speed_mps: float) -> float:
    return round(min(cfg.MAX_SPEED_MPS, max(cfg.MIN_SPEED_MPS, speed_mps)), 2)


class GestureController:
    """detector.py의 카메라 루프에서 매 프레임 on_frame()으로 호출되는 상태 머신.

    정지·재출발·가속·감속·하트비트 전부 dashboard_client(PC web_dashboard 경유)로
    통일한다 — 호출부(detector.py)에서 주입받는다. 2026-08-22 이전엔 정지/재출발만
    vehicle_control_client(1호기 직접, PC 안 거침)로 분리했었으나, PC 경유 방식으로
    되돌리기로 결정해 단일 클라이언트로 통합함 (design/README.md §3-1-1).
    이 모듈은 통신 방식을 모르고 클라이언트 모듈의 함수만 호출한다 (테스트 시 모킹 용이).
    """

    def __init__(self, dashboard_client) -> None:
        self._dashboard_client = dashboard_client

        self._fist_streak = 0
        self._fist_miss_streak = 0

        # 정지를 요구 중인 사유들 — 비어있지 않은 동안은 정지 유지, 전부 비워지면 재출발
        self._stop_reasons: set[str] = set()
        self._speed_before_stop: float | None = None

        self._accel_streak = 0
        self._decel_streak = 0
        self._speed_cooldown_until = 0.0

        # 매번 GET으로 재조회하지 않도록 로컬에 캐싱 (README §3-2-3 문제 1 개선).
        # 시작 시 1회, 이후 성공적인 응답마다 갱신.
        self._known_speed_mps: float | None = None

        self._heartbeat_stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @property
    def is_stopped(self) -> bool:
        return bool(self._stop_reasons)

    # --- 하트비트 (README §3-1-4 문제 4: 워치독 1.5초 < 쿨타임 2초) ---

    def start_heartbeat(self) -> None:
        if self._heartbeat_thread is not None:
            return
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop_event.is_set():
            try:
                self._dashboard_client.send_heartbeat(cfg.DASHBOARD_URL)
            except Exception as exc:  # noqa: BLE001 - 하트비트 실패는 로그만, 재시도 계속
                print(f"[gesture] 하트비트 실패 (재시도 예정): {exc}")
            self._heartbeat_stop_event.wait(cfg.HEARTBEAT_INTERVAL_S)

    # --- 정지 사유 등록/해제 (표지판 검출기와 공용 진입점) ---

    def request_stop(self, reason: str) -> None:
        """reason이 정지를 요구 중이라고 등록. 처음 등록되는 사유일 때만 실제 STOP 전송."""
        was_stopped = self.is_stopped
        self._stop_reasons.add(reason)
        if not was_stopped:
            self._send_stop()

    def release_stop(self, reason: str) -> None:
        """reason의 정지 요구를 해제. 모든 사유가 해제됐을 때만 실제 START(재개) 전송."""
        self._stop_reasons.discard(reason)
        if not self.is_stopped:
            self._send_resume()

    def _send_stop(self) -> None:
        # 재출발 시 복원할 속도를 정지 "직전"에 저장 (README §3-1-2)
        self._speed_before_stop = self._known_speed_mps
        try:
            self._dashboard_client.stop(cfg.DASHBOARD_URL)
            print(f"[control] STOP 전송 완료 (사유={self._stop_reasons})")
        except Exception as exc:  # noqa: BLE001
            print(f"[control] STOP 전송 실패: {exc}")

    def _send_resume(self) -> None:
        resume_speed = self._speed_before_stop or cfg.MIN_SPEED_MPS
        try:
            result = self._dashboard_client.set_speed(cfg.DASHBOARD_URL, resume_speed)
            print(f"[control] START 전송 완료 (속도 {resume_speed} m/s 복원)")
        except Exception as exc:  # noqa: BLE001
            print(f"[control] START(재출발) 전송 실패: {exc}")
            # 전송 실패 시 사유가 이미 비워진 상태라 실제로는 정지 요구가 없는데도
            # 물리적으로는 아직 STOPPED일 수 있음 — 다음 프레임에서 재시도되도록
            # 사유를 그대로 두지 않고 로그만 남김 (재시도는 호출부의 디바운스가 다시 유발)
            return
        self._known_speed_mps = result.get("target_speed_mps", resume_speed)

    # --- 프레임 처리 (제스처 전용 입력) ---

    def on_frame(self, gesture: tuple[str, float] | None) -> None:
        """gesture_recognizer.recognize()의 결과를 매 프레임 전달받아 상태를 갱신."""
        label = gesture[0] if gesture is not None else None

        self._handle_fist(label)
        # 정지 상태(사유 무관)에서는 가속/감속 제스처를 무시 (정지가 최우선)
        if not self.is_stopped:
            self._handle_speed(label)

    def _handle_fist(self, label: str | None) -> None:
        """주먹은 '정지 버튼'일 뿐 — 손을 풀었다고 자동으로 재출발시키지 않는다
        (요청 범위: 주먹=정지만. 재출발은 사람이 대시보드에서 직접 하는 별개 동작).
        표지판(STOP_SIGN)이 사라졌을 때 자동 재출발하는 건 stop_sign 원래 기능이라
        그대로 유지하지만, FIST는 request_stop만 쓰고 release_stop(자동 재출발 포함)은
        쓰지 않는다."""
        if "FIST" not in self._stop_reasons:
            self._fist_streak = self._fist_streak + 1 if label == cfg.GESTURE_STOP else 0
            if self._fist_streak >= cfg.GESTURE_DEBOUNCE_N:
                self.request_stop("FIST")
                self._fist_streak = 0
        else:
            # 다음 주먹을 다시 인식할 수 있도록 내부 사유만 비움 — STOP/START 어느 쪽도 보내지 않음
            self._fist_miss_streak = self._fist_miss_streak + 1 if label != cfg.GESTURE_STOP else 0
            if self._fist_miss_streak >= cfg.GESTURE_DEBOUNCE_M:
                self._stop_reasons.discard("FIST")
                self._fist_miss_streak = 0

    # --- 가속/감속(따봉/역따봉) ---

    def _handle_speed(self, label: str | None) -> None:
        self._accel_streak = self._accel_streak + 1 if label == cfg.GESTURE_ACCELERATE else 0
        self._decel_streak = self._decel_streak + 1 if label == cfg.GESTURE_DECELERATE else 0

        now = time.monotonic()
        if now < self._speed_cooldown_until:
            return

        if self._accel_streak >= cfg.GESTURE_DEBOUNCE_N:
            self._step_speed(+cfg.SPEED_STEP_MPS)
            self._accel_streak = 0
        elif self._decel_streak >= cfg.GESTURE_DEBOUNCE_N:
            self._step_speed(-cfg.SPEED_STEP_MPS)
            self._decel_streak = 0

    def _step_speed(self, delta_mps: float) -> None:
        if self._known_speed_mps is None:
            self._sync_known_speed()
        current = self._known_speed_mps if self._known_speed_mps is not None else cfg.MIN_SPEED_MPS

        # MIN_SPEED_MPS를 하한으로 클램프 — "감속으로 0에 닿으면 정지로 전환"하는 안은
        # 검토했으나, 정지 상태에서 빠져나오려면 별도의 "재개" 트리거가 필요해져
        # 주먹(FIST) 해제 조건과 뒤엉키는 문제가 있어 채택하지 않음(디자인 노트 참고).
        # 실제 정지가 필요하면 주먹 제스처를 쓰면 되므로, 감속은 하한에서 그냥 멈춤(no-op).
        raw_new_speed = current + delta_mps
        if raw_new_speed <= cfg.MIN_SPEED_MPS and delta_mps < 0:
            print(f"[gesture] 이미 최저 속도({cfg.MIN_SPEED_MPS} m/s) — 추가 감속 무시")
            self._speed_cooldown_until = time.monotonic() + cfg.SPEED_COOLDOWN_S
            return

        new_speed = _clamp_speed(raw_new_speed)
        try:
            result = self._dashboard_client.set_speed(cfg.DASHBOARD_URL, new_speed)
            print(f"[gesture] 속도 변경 {current} -> {new_speed} m/s")
        except Exception as exc:  # noqa: BLE001
            print(f"[gesture] 속도 변경 요청 실패: {exc}")
            self._speed_cooldown_until = time.monotonic() + cfg.SPEED_COOLDOWN_S
            return

        rover = result.get("rover", {})
        self._known_speed_mps = rover.get("target_speed_mps", new_speed)
        self._speed_cooldown_until = time.monotonic() + cfg.SPEED_COOLDOWN_S

    def _sync_known_speed(self) -> None:
        try:
            status = self._dashboard_client.get_status(cfg.DASHBOARD_URL)
            self._known_speed_mps = status.get("target_speed_mps") or cfg.MIN_SPEED_MPS
        except Exception as exc:  # noqa: BLE001
            print(f"[gesture] 초기 속도 조회 실패, 기본값 사용: {exc}")
            self._known_speed_mps = cfg.MIN_SPEED_MPS
