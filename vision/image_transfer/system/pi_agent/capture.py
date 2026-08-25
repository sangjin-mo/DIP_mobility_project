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
from fastapi.responses import JSONResponse, Response
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

# 순찰 촬영 on/off. 대시보드의 START가 켜고 STOP이 끈다 (POST /capture/start, /capture/stop).
# 기본값은 꺼짐 — 프로세스가 떠 있다는 것만으로 촬영이 시작되면 안 된다.
_capture_lock = threading.Lock()
_capture_armed = False
_armed_patrol_id: str | None = None
_armed_at_monotonic: float | None = None


class StartCaptureRequest(BaseModel):
    patrol_id: str | None = None


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


def arm_capture(patrol_id: str | None) -> dict:
    """순찰 촬영을 켠다. 대시보드 START가 호출."""
    global _capture_armed, _armed_patrol_id, _armed_at_monotonic
    with _capture_lock:
        _capture_armed = True
        _armed_patrol_id = patrol_id
        _armed_at_monotonic = time.monotonic()
        return {"armed": True, "patrol_id": _armed_patrol_id}


def disarm_capture() -> dict:
    """순찰 촬영을 끈다. 대시보드 STOP이 호출."""
    global _capture_armed, _armed_patrol_id, _armed_at_monotonic
    with _capture_lock:
        was_armed = _capture_armed
        previous_patrol_id = _armed_patrol_id
        _capture_armed = False
        _armed_patrol_id = None
        _armed_at_monotonic = None
        return {"armed": False, "was_armed": was_armed, "patrol_id": previous_patrol_id}


def capture_state() -> dict:
    with _capture_lock:
        return {"armed": _capture_armed, "patrol_id": _armed_patrol_id}


def should_capture_now() -> bool:
    """지금 자동 촬영을 해야 하는지.

    STOP 신호를 못 받은 채로 촬영이 계속되면 디스크가 찬다. 예전에는 차량 상태
    API를 2초마다 폴링해서 이걸 막았지만, 그 값은 "순찰 중"이 아니라 "주행 프로세스가
    살아있음"에 가까워서 순찰과 무관하게 촬영이 돌았다(실측: 순찰이 없는
    17:29-17:31 구간에도 계속 저장됨). 이제는 명시적인 START/STOP만 촬영을
    제어하고, 안전장치는 최대 순찰 시간으로만 둔다.
    """
    global _capture_armed, _armed_patrol_id, _armed_at_monotonic
    with _capture_lock:
        if not _capture_armed:
            return False
        if _armed_at_monotonic is None:
            return True
        if time.monotonic() - _armed_at_monotonic > config.MAX_CAPTURE_SESSION_SEC:
            print(
                f"촬영 시작 후 {config.MAX_CAPTURE_SESSION_SEC}초 경과 — STOP 신호를 "
                "못 받은 것으로 보고 자동 중지합니다.",
                flush=True,
            )
            _capture_armed = False
            _armed_patrol_id = None
            _armed_at_monotonic = None
            return False
        return True


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

    더 이상 자동 촬영의 on/off를 결정하지 않는다. 이 값은 "순찰 중"이 아니라
    "주행 프로세스가 살아있음"에 가까워서, 순찰과 무관한 구간에도 촬영이 계속
    돌고 정작 순찰 중에는 안 돌 수 있었다. 촬영 제어는 이제 대시보드가
    POST /capture/start · /capture/stop으로 직접 보낸다(should_capture_now 참고).

    상태 표시·진단 용도로만 남겨둔다. 호출하는 쪽이 없으면 지워도 된다.
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


@control_app.post("/capture/start")
def start_capture(request: StartCaptureRequest):
    """대시보드 START → 순찰 촬영 시작. 즉시 반영된다(폴링 대기 없음)."""
    result = arm_capture(request.patrol_id)
    print(f"순찰 촬영 시작 (patrol_id={request.patrol_id})", flush=True)
    return {"status": "ok", **result}


@control_app.post("/capture/stop")
def stop_capture():
    """대시보드 STOP → 순찰 촬영 중지."""
    result = disarm_capture()
    print(f"순찰 촬영 중지 (patrol_id={result['patrol_id']})", flush=True)
    return {"status": "ok", **result}


@control_app.get("/capture/state")
def get_capture_state():
    """지금 순찰 촬영 중인지. 대시보드가 START 직후 확인용으로 쓴다."""
    return {"status": "ok", **capture_state()}


@control_app.get("/latest-frame")
def latest_frame():
    """카메라를 직접 열지 않는 다른 기능(예: stop_sign)이 이 프로세스가 이미 들고 있는
    최신 프레임을 받아가는 용도. 물리 카메라가 1대뿐이라 두 기능이 각자 cv2.VideoCapture를
    열면 충돌하므로(카메라를 실제로 여는 건 이 프로세스 하나로 통일), HTTP로 프레임만 공유한다.
    """
    with _frame_lock:
        frame = None if _latest_frame is None else _latest_frame.copy()
    if frame is None:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "reason": "아직 카메라 프레임이 준비되지 않았습니다."},
        )
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return JSONResponse(status_code=500, content={"status": "error", "reason": "인코딩 실패"})
    return Response(content=buf.tobytes(), media_type="image/jpeg")


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
    last_save_ts = 0.0  # 저장 주기 게이팅용 — 프레임 읽기 자체는 이 값과 무관하게 매번 수행
    was_capturing = False

    try:
        while True:
            # 카메라 읽기 + _latest_frame 갱신은 촬영 주기와 무관하게 매 루프 수행한다.
            # (stop_sign 등 인식 기능이 /latest-frame으로 이 프레임을 받아가는데, 인식은
            # 주기 제한이 없어야 하고 "저장"만 주기를 지켜야 하기 때문 — 저장 여부만 아래서
            # 시간차로 게이팅한다. cap.read()가 카메라의 실제 프레임레이트로 자연히 블로킹됨.)
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
            capturing = should_capture_now()
            if capturing != was_capturing:
                print(f"순찰 촬영 {'시작' if capturing else '중지'}", flush=True)
                was_capturing = capturing

            if not capturing:
                continue

            if now_ts - last_disk_check >= config.DISK_CHECK_INTERVAL_SEC:
                ensure_disk_space()
                last_disk_check = now_ts

            if now_ts - last_save_ts < get_capture_interval():
                continue
            last_save_ts = now_ts

            try:
                result = save_frame_now(frame)
            except OSError as e:
                print(f"저장 실패 (디스크 오류): {e}", flush=True)
                continue

            total_saved += 1
            print(f"저장: {result['filepath']}", flush=True)

    except KeyboardInterrupt:
        print(f"\n종료. 총 {total_saved}장 저장됨.", flush=True)

    finally:
        cap.release()


if __name__ == "__main__":
    main()
