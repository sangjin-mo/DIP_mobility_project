# 라즈베리파이 2호기 — 정지 표지판 인식 메인 루프
# 흐름: 측면 카메라 → YOLO 추론 → 디바운스(N/M) → GPIO 출력, 쿨다운(§3-1), driving 플래그 체크(§3-2)
# 시퀀스 다이어그램: design/seq_detect_stop.svg

import threading
import time
from datetime import datetime

import cv2
import uvicorn
from gpiozero import DigitalOutputDevice
from ultralytics import YOLO

import config
from state_api import app, vehicle_state


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def run_state_api() -> None:
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="warning")


def detect_stop_sign(model: YOLO, frame) -> bool:
    result = model(frame, verbose=False)[0]
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

    model = YOLO(config.MODEL_PATH)
    gpio_out = DigitalOutputDevice(config.GPIO_OUT_PIN)

    camera = cv2.VideoCapture(config.SIDE_CAMERA_INDEX)
    if not camera.isOpened():
        raise RuntimeError(f"측면 카메라(index={config.SIDE_CAMERA_INDEX})를 열 수 없습니다")

    detect_streak = 0
    miss_streak = 0
    is_stopped = False
    cooldown_until = 0.0
    was_driving = None  # 상태 전환 로그를 한 번만 찍기 위한 이전 값

    print(f"[{_ts()}] [detector] 시작 — camera={config.SIDE_CAMERA_INDEX}, gpio_out={config.GPIO_OUT_PIN}, "
          f"state_api=http://{config.API_HOST}:{config.API_PORT}/vehicle-state")

    cooldown_logged = False  # 쿨다운 중 억제 로그를 한 번만 찍기 위한 플래그

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

            ok, frame = camera.read()
            if not ok:
                continue

            detected = detect_stop_sign(model, frame)

            if not is_stopped:
                # Loop 1: 표지판이 인식될 때까지 (design/seq_detect_stop.svg ①~③)
                detect_streak = detect_streak + 1 if detected else 0

                now = time.monotonic()
                if detect_streak >= config.DEBOUNCE_N:
                    if now >= cooldown_until:
                        # ④~⑥: 정지 신호 전송, 동시에 쿨다운 시작 (§3-1)
                        # TODO: 쿨다운을 "HIGH 전송 시점"이 아니라 "LOW 전송(재출발) 시점"부터
                        # 시작하는 게 나을지는 실제 1호기 재출발 방식이 정해진 뒤 재검토 필요.
                        gpio_out.on()
                        is_stopped = True
                        detect_streak = 0
                        miss_streak = 0
                        cooldown_until = now + config.COOLDOWN_SECONDS
                        cooldown_logged = False
                        print(f"[{_ts()}] [detector] GPIO HIGH — 정지 신호 전송 (쿨다운 {config.COOLDOWN_SECONDS}s 시작)")
                    elif not cooldown_logged:
                        remaining = cooldown_until - now
                        print(f"[{_ts()}] [detector] 쿨다운 중이라 재신호 억제 (남은 시간 {remaining:.1f}s)")
                        cooldown_logged = True
            else:
                # Loop 2: 표지판이 안 잡힐 때까지, 정지 상태 유지 중 (⑦~⑨)
                miss_streak = miss_streak + 1 if not detected else 0

                if miss_streak >= config.DEBOUNCE_M:
                    # ⑩~⑫: 정지 해제 신호 전송
                    gpio_out.off()
                    is_stopped = False
                    miss_streak = 0
                    print(f"[{_ts()}] [detector] GPIO LOW — 정지 해제 신호 전송")
    finally:
        camera.release()
        gpio_out.close()


if __name__ == "__main__":
    main()
