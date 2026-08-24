# 라즈베리파이 2호기 — 정지 표지판 인식 + 제스처 인식 통합 메인 루프
# 흐름: 측면 카메라(공유) -> YOLO 추론(표지판)/MediaPipe 추론(손동작) -> 디바운스(N/M)
#       -> GestureController(정지 사유 관리, 정지/재출발/가속/감속/하트비트 전부 PC 경유)
# 시퀀스 다이어그램: design/seq_detect_stop.svg (GPIO 출력이던 부분은 이제 PC
# web_dashboard의 "■ 정지" 버튼과 동일한 HTTP 호출로 대체됨 — mediapipe/design/README.md §3-1-1 참고)
#
# 카메라를 features/mediapipe(제스처 인식)와 공유하기로 해서, 제스처 인식 로직을
# 별도 프로세스로 두지 않고 이 루프 안에서 같은 프레임에 대해 함께 실행한다.

import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from ultralytics import YOLO

import config
import dashboard_client
from state_api import app, vehicle_state

# features/mediapipe/system/pi2_gesture는 별도 폴더(다른 기능의 소유)라 sys.path에 추가해서 가져옴.
# 모듈 이름 충돌 방지를 위해 그쪽 설정 파일은 gesture_config.py로 명명돼 있음(이 폴더의 config.py와 별개).
_GESTURE_DIR = Path(__file__).resolve().parents[3] / "mediapipe" / "system" / "pi2_gesture"
sys.path.insert(0, str(_GESTURE_DIR))
from gesture_controller import GestureController  # noqa: E402
import gesture_config  # noqa: E402

# gesture_recognizer.py는 실제 mediapipe 패키지를 import함 — 이 Pi에서는 SIGILL로
# 크래시하는 경우가 있어(config.GESTURE_RECOGNITION_ENABLED 참고) 꺼져 있을 때는
# import 자체를 안 해서, mediapipe 미설치 환경에서도 표지판 인식만은 돌아가게 함.
if config.GESTURE_RECOGNITION_ENABLED:
    from gesture_recognizer import GestureRecognizerWrapper  # noqa: E402


def poll_driving_state() -> None:
    """PC web_dashboard의 상태를 주기적으로 조회해 vehicle_state를 갱신한다.

    STOP/START(vehicle_control_client)와 달리 이건 "지금 추론을 돌려야 하는지"
    판단용이라 안전 필수 기능이 아님 — 기존 설계 원칙대로 PC 경유(dashboard_client)로
    충분함. 조회 실패 시엔 안전 쪽으로 driving=False (추론 생략)로 둠.
    (design/README.md §3-2, mediapipe/design/README.md §3-1-보충 참고)
    """
    while True:
        try:
            status = dashboard_client.get_status(gesture_config.DASHBOARD_URL)
            vehicle_state.set_driving(status.get("state") == "RUNNING")
        except Exception as exc:  # noqa: BLE001
            vehicle_state.set_driving(False)
            print(f"[{_ts()}] [detector] 주행 상태 폴링 실패, driving=False로 간주: {exc}")
        time.sleep(config.DRIVING_STATE_POLL_INTERVAL_S)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def run_state_api() -> None:
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="warning")


def fetch_frame():
    """카메라를 직접 열지 않고, image_transfer의 capture.py가 이미 들고 있는 최신
    프레임을 로컬 HTTP로 받아온다 (물리 카메라 1대를 두 기능이 공유, config.py 참고).
    실패 시 None을 반환 — 기존 camera.read() 실패(ok=False)와 동일하게 다룬다.
    """
    try:
        with urllib.request.urlopen(
            config.CAPTURE_SERVICE_URL, timeout=config.CAPTURE_SERVICE_TIMEOUT_SEC
        ) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    return frame


def detect_stop_sign(model: YOLO, frame) -> bool:
    result = model(frame, imgsz=config.INFERENCE_IMGSZ, verbose=False)[0]
    for box in result.boxes:
        if (
            int(box.cls[0]) == config.STOP_SIGN_CLASS_ID
            and float(box.conf[0]) >= config.CONFIDENCE_THRESHOLD
        ):
            return True
    return False


def main() -> None:
    api_thread = threading.Thread(target=run_state_api, daemon=True)
    api_thread.start()

    poll_thread = threading.Thread(target=poll_driving_state, daemon=True)
    poll_thread.start()

    model = YOLO(config.MODEL_PATH)
    controller = GestureController(dashboard_client)
    gesture_recognizer = GestureRecognizerWrapper() if config.GESTURE_RECOGNITION_ENABLED else None

    controller.start_heartbeat()

    detect_streak = 0
    miss_streak = 0
    sign_is_stopped = False
    cooldown_until = 0.0
    was_driving = None  # 상태 전환 로그를 한 번만 찍기 위한 이전 값
    cooldown_logged = False  # 쿨다운 중 억제 로그를 한 번만 찍기 위한 플래그
    start_time = time.monotonic()

    # 정지 반응 지연 진단용 — 프레임당 처리 시간을 주기적으로 평균 내서 로그 (INFERENCE_IMGSZ
    # 등 튜닝 효과를 실측으로 확인하기 위함, 매 프레임 찍으면 로그가 너무 많아져서 30프레임마다)
    frame_time_sum = 0.0
    frame_time_count = 0
    loop_prev_ts = time.monotonic()

    print(f"[{_ts()}] [detector] 시작 — 카메라는 image_transfer의 capture.py가 소유, "
          f"프레임은 {config.CAPTURE_SERVICE_URL}에서 받아옴, "
          f"driving 상태는 {gesture_config.DASHBOARD_URL}/api/control/status 폴링으로 판단 "
          f"({config.DRIVING_STATE_POLL_INTERVAL_S}s 주기), "
          f"state_api=http://{config.API_HOST}:{config.API_PORT}/vehicle-state(수동 테스트용, 병행 유지)")

    try:
        while True:
            driving = vehicle_state.is_driving()
            if driving != was_driving:
                print(f"[{_ts()}] [detector] driving={driving} 로 전환")
                was_driving = driving

            if not driving:
                # 주행 중이 아니면 추론 자체를 생략 (§3-2, 자원 절약)
                time.sleep(0.2)
                continue

            frame = fetch_frame()
            if frame is None:
                continue

            # --- 표지판 인식 (기존 로직, GPIO 대신 controller에 위임) ---
            sign_detected = detect_stop_sign(model, frame)

            now_ts = time.monotonic()
            frame_time_sum += now_ts - loop_prev_ts
            frame_time_count += 1
            loop_prev_ts = now_ts
            if frame_time_count >= 30:
                avg_ms = (frame_time_sum / frame_time_count) * 1000
                print(f"[{_ts()}] [detector] 최근 {frame_time_count}프레임 평균 처리시간 {avg_ms:.0f}ms/frame "
                      f"(imgsz={config.INFERENCE_IMGSZ}, N={config.DEBOUNCE_N}이면 정지확정까지 약 {avg_ms * config.DEBOUNCE_N:.0f}ms)")
                frame_time_sum = 0.0
                frame_time_count = 0

            if not sign_is_stopped:
                detect_streak = detect_streak + 1 if sign_detected else 0
                now = time.monotonic()
                if detect_streak >= config.DEBOUNCE_N:
                    if now >= cooldown_until:
                        controller.request_stop("STOP_SIGN")
                        sign_is_stopped = True
                        detect_streak = 0
                        miss_streak = 0
                        cooldown_until = now + config.COOLDOWN_SECONDS
                        cooldown_logged = False
                        print(f"[{_ts()}] [detector] 표지판 확정 — STOP 요청 (쿨다운 {config.COOLDOWN_SECONDS}s 시작)")
                    elif not cooldown_logged:
                        remaining = cooldown_until - now
                        print(f"[{_ts()}] [detector] 쿨다운 중이라 재신호 억제 (남은 시간 {remaining:.1f}s)")
                        cooldown_logged = True
            else:
                miss_streak = miss_streak + 1 if not sign_detected else 0
                if miss_streak >= config.DEBOUNCE_M:
                    controller.release_stop("STOP_SIGN")
                    sign_is_stopped = False
                    miss_streak = 0
                    print(f"[{_ts()}] [detector] 표지판 해제 — STOP 요청 철회")

            # --- 제스처 인식 (같은 프레임 재사용, GESTURE_RECOGNITION_ENABLED일 때만) ---
            if gesture_recognizer is not None:
                timestamp_ms = int((time.monotonic() - start_time) * 1000)
                gesture = gesture_recognizer.recognize(frame, timestamp_ms)
                controller.on_frame(gesture)
    finally:
        controller.stop_heartbeat()
        if gesture_recognizer is not None:
            gesture_recognizer.close()


if __name__ == "__main__":
    main()
