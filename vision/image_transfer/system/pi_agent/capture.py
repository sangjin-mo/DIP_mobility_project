import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import cv2
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config

# capture.py 프로세스가 카메라 핸들을 직접 들고 있으므로, 수동 단발 촬영 API도
# 별도 프로세스(upload_server.py)가 아니라 여기서 같은 프로세스 안에서 서비스한다.
# _frame_lock: 자동 루프가 매초 갱신하는 최신 프레임을 수동 촬영 API와 안전하게 공유
# _seq_lock: 자동 루프와 수동 촬영이 같은 "초" 안에서 파일명을 만들 때 순번이 겹치지 않게 보호
# _interval_lock: 실행 중 웹에서 촬영 주기를 바꿀 때 루프와 API가 동시에 값을 안 건드리게 보호
control_app = FastAPI()
_frame_lock = threading.Lock()
_latest_frame = None
_seq_lock = threading.Lock()
_last_second = None
_seq = 0
_interval_lock = threading.Lock()
_capture_interval_sec = config.CAPTURE_INTERVAL_SEC


class SetIntervalRequest(BaseModel):
    interval_sec: float = Field(gt=0)


def get_capture_interval() -> float:
    with _interval_lock:
        return _capture_interval_sec


def set_capture_interval(interval_sec: float) -> float:
    global _capture_interval_sec
    clamped = max(interval_sec, config.MIN_CAPTURE_INTERVAL_SEC)
    with _interval_lock:
        _capture_interval_sec = clamped
    return clamped


def make_filename(dt: datetime, cam_id: str, seq: int) -> str:
    return f"{dt.strftime('%Y%m%d_%H%M%S')}_{cam_id}_{seq:03d}.jpg"


def get_day_dir(dt: datetime) -> str:
    day_dir = os.path.join(config.BASE_DIR, dt.strftime("%Y-%m-%d"))
    os.makedirs(day_dir, exist_ok=True)
    return day_dir


def save_image(filepath: str, frame) -> bool:
    # cv2.imwrite silently fails on Windows when the path contains non-ASCII
    # characters (e.g. Korean folder names), so encode in memory and write
    # the bytes ourselves instead.
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return False
    buf.tofile(filepath)
    return True


def next_filepath(now: datetime) -> tuple[str, str]:
    """같은 초 안에서 자동/수동 촬영이 겹쳐도 순번이 안 꼬이게 잠금으로 보호."""
    global _last_second, _seq
    current_second = now.strftime("%Y%m%d_%H%M%S")
    with _seq_lock:
        if current_second == _last_second:
            _seq += 1
        else:
            _seq = 1
            _last_second = current_second
        seq = _seq
    day_dir = get_day_dir(now)
    filename = make_filename(now, config.CAMERA_ID, seq)
    return day_dir, filename


def save_frame_now(frame) -> dict:
    """지금 이 프레임을 즉시 저장. 자동 루프와 수동 촬영 API가 공용으로 사용."""
    now = datetime.now()
    day_dir, filename = next_filepath(now)
    filepath = os.path.join(day_dir, filename)
    saved = save_image(filepath, frame)
    if not saved:
        raise OSError(f"저장 실패: {filepath}")
    return {"filename": filename, "filepath": filepath}


def free_space_mb(path: str) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 * 1024)


def ensure_disk_space():
    """여유 공간이 부족하면 가장 오래된 날짜 폴더부터 삭제."""
    while free_space_mb(config.BASE_DIR) < config.MIN_FREE_DISK_MB:
        day_dirs = sorted(
            d for d in os.listdir(config.BASE_DIR)
            if os.path.isdir(os.path.join(config.BASE_DIR, d))
        )
        if not day_dirs:
            print("저장 공간 부족하지만 삭제할 폴더가 없음", flush=True)
            break

        oldest = os.path.join(config.BASE_DIR, day_dirs[0])
        shutil.rmtree(oldest)
        print(f"저장 공간 부족으로 오래된 폴더 삭제: {oldest}", flush=True)


def is_vehicle_running() -> bool:
    """통합 대시보드의 차량 상태 API(RUNNING/STOPPED)를 읽기 전용으로 조회.

    web_dashboard 코드는 건드리지 않고, 이미 있는 GET /api/control/status만 호출한다.
    응답을 못 받거나 형식이 이상하면 config.FAIL_OPEN_WHEN_STATUS_UNKNOWN 값을 따른다
    (기본 False = 확인 안 되면 정지로 간주해서 촬영 안 함).
    """
    request = urllib.request.Request(config.CONTROL_STATUS_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=config.CONTROL_STATUS_TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return config.FAIL_OPEN_WHEN_STATUS_UNKNOWN
    return payload.get("state") == "RUNNING"


@control_app.post("/capture-now")
def capture_now():
    """수동 단발 촬영. 자동 주기 촬영의 on/off(차량 구동 상태)와 무관하게 항상 동작."""
    with _frame_lock:
        frame = None if _latest_frame is None else _latest_frame.copy()
    if frame is None:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "reason": "아직 카메라 프레임이 준비되지 않았습니다."},
        )
    try:
        result = save_frame_now(frame)
    except OSError as e:
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})
    print(f"수동 촬영: {result['filepath']}", flush=True)
    return {"status": "ok", "filename": result["filename"]}


@control_app.get("/interval")
def get_interval():
    return {"interval_sec": get_capture_interval(), "min_interval_sec": config.MIN_CAPTURE_INTERVAL_SEC}


@control_app.post("/set-interval")
def set_interval(request: SetIntervalRequest):
    applied = set_capture_interval(request.interval_sec)
    clamped_up = applied != request.interval_sec
    print(f"촬영 주기 변경: {applied}초{' (최소값으로 보정됨)' if clamped_up else ''}", flush=True)
    return {"interval_sec": applied, "requested_sec": request.interval_sec, "clamped": clamped_up}


def run_control_server():
    uvicorn.run(control_app, host="0.0.0.0", port=config.CAPTURE_CONTROL_PORT, log_level="warning")


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"카메라를 열 수 없습니다 (index={index})")
    return cap


def reconnect_camera(cap: cv2.VideoCapture) -> cv2.VideoCapture:
    """카메라 재연결을 계속 시도. 성공할 때까지 블로킹."""
    cap.release()
    while True:
        print("카메라 재연결 시도 중...", flush=True)
        try:
            return open_camera(config.CAMERA_INDEX)
        except RuntimeError:
            time.sleep(config.RECONNECT_RETRY_DELAY_SEC)


def main():
    global _latest_frame

    os.makedirs(config.BASE_DIR, exist_ok=True)
    cap = open_camera(config.CAMERA_INDEX)

    threading.Thread(target=run_control_server, daemon=True).start()
    print(f"수동 촬영 API 대기 중: http://0.0.0.0:{config.CAPTURE_CONTROL_PORT}/capture-now", flush=True)

    total_saved = 0
    consecutive_failures = 0
    last_disk_check = 0.0
    last_state_check = 0.0
    vehicle_running = False

    try:
        while True:
            ret, frame = cap.read()

            if not ret:
                consecutive_failures += 1
                print(f"프레임 획득 실패 ({consecutive_failures}/{config.MAX_CONSECUTIVE_FAILURES})", flush=True)
                if consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
                    cap = reconnect_camera(cap)
                    consecutive_failures = 0
                continue

            consecutive_failures = 0

            with _frame_lock:
                _latest_frame = frame

            now_ts = time.monotonic()
            if now_ts - last_state_check >= config.CONTROL_STATUS_POLL_SEC:
                new_running = is_vehicle_running()
                if new_running != vehicle_running:
                    print(
                        f"차량 상태 변경 감지 → 촬영 {'시작' if new_running else '중지'}",
                        flush=True,
                    )
                vehicle_running = new_running
                last_state_check = now_ts

            if not vehicle_running:
                time.sleep(get_capture_interval())
                continue

            if now_ts - last_disk_check >= config.DISK_CHECK_INTERVAL_SEC:
                ensure_disk_space()
                last_disk_check = now_ts

            try:
                result = save_frame_now(frame)
            except OSError as e:
                print(f"저장 실패 (디스크 오류): {e}", flush=True)
                continue

            total_saved += 1
            print(f"저장: {result['filepath']}", flush=True)

            time.sleep(get_capture_interval())

    except KeyboardInterrupt:
        print(f"\n종료. 총 {total_saved}장 저장됨.", flush=True)

    finally:
        cap.release()


if __name__ == "__main__":
    main()
