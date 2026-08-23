"""Standalone state receiver for the webcam Raspberry Pi.

Run this next to (not inside) the vision team's existing capture/upload
processes. The newest rover state is stored atomically as JSON so camera or
vision code can read it without importing the web dashboard.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

STATE_FILE = Path(os.getenv("VISION_DRIVE_STATE_FILE", "vision_drive_state.json"))
SHARED_TOKEN = os.getenv("VISION_DRIVE_STATE_TOKEN")
CAPTURE_DIR = Path(
    os.getenv(
        "VISION_CAPTURE_DIR",
        str(Path(__file__).resolve().parent.parent / "vision/image_transfer/system/pi_agent/images"),
    )
)
CAMERA_INDEX = int(os.getenv("VISION_CAMERA_INDEX", "0"))
CAMERA_ID = os.getenv("VISION_CAMERA_ID", "cam01")
CAPTURE_INTERVAL_S = float(os.getenv("VISION_CAPTURE_INTERVAL_S", "1.0"))


class DriveStateEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    state: str = Field(pattern="^(RUNNING|STOPPED|EMERGENCY)$")
    previous_state: str | None = Field(default=None, pattern="^(RUNNING|STOPPED|EMERGENCY)$")
    target_speed_mps: float | None = Field(default=None, ge=0, le=1.0)
    changed_at_ms: int = Field(gt=0)
    source: str = "web_dashboard"


class CaptureModeRequest(BaseModel):
    enabled: bool


class CaptureWorker:
    """Own the webcam only while dashboard capture mode is enabled."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._saved_count = 0
        self._last_filename: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self._active,
                "saved_count": self._saved_count,
                "last_filename": self._last_filename,
                "last_error": self._last_error,
                "capture_dir": str(CAPTURE_DIR),
                "interval_s": CAPTURE_INTERVAL_S,
            }

    def set_enabled(self, enabled: bool) -> dict:
        if enabled:
            self._start()
        else:
            self._stop()
        return self.status()

    def _start(self) -> None:
        with self._lock:
            if self._active:
                return
            self._stop_event.clear()
            self._last_error = None
            self._active = True
            self._thread = threading.Thread(target=self._run, name="vision-capture", daemon=True)
            self._thread.start()

    def _stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=max(3.0, CAPTURE_INTERVAL_S + 1.0))
        with self._lock:
            self._active = False
            self._thread = None

    def _run(self) -> None:
        camera = None
        try:
            import cv2

            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            camera = cv2.VideoCapture(CAMERA_INDEX)
            if not camera.isOpened():
                raise RuntimeError(f"카메라를 열 수 없습니다 (index={CAMERA_INDEX})")

            last_second = None
            sequence = 0
            while not self._stop_event.is_set():
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("웹캠 프레임을 읽지 못했습니다.")
                now = datetime.now()
                current_second = now.strftime("%Y%m%d_%H%M%S")
                sequence = sequence + 1 if current_second == last_second else 1
                last_second = current_second
                relative_path = self._write_frame(cv2, frame, now, sequence)
                with self._lock:
                    self._saved_count += 1
                    self._last_filename = relative_path
                self._stop_event.wait(CAPTURE_INTERVAL_S)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            if camera is not None:
                camera.release()
            with self._lock:
                self._active = False

    @staticmethod
    def _write_frame(cv2_module, frame, now: datetime, sequence: int) -> str:
        day = now.strftime("%Y-%m-%d")
        day_dir = CAPTURE_DIR / day
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{CAMERA_ID}_{sequence:03d}.jpg"
        encoded, buffer = cv2_module.imencode(".jpg", frame)
        if not encoded:
            raise RuntimeError("웹캠 이미지를 JPEG로 변환하지 못했습니다.")
        (day_dir / filename).write_bytes(buffer.tobytes())
        return str(Path(day) / filename)


app = FastAPI(title="Webcam Pi Drive State Receiver")
_latest: dict | None = None
capture_worker = CaptureWorker()


def _authorise(authorization: str | None) -> None:
    if SHARED_TOKEN and authorization != f"Bearer {SHARED_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")


def _save_atomically(payload: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(f"{STATE_FILE.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


@app.post("/api/drive-state")
def receive_drive_state(
    event: DriveStateEvent,
    authorization: str | None = Header(default=None),
) -> dict:
    global _latest
    _authorise(authorization)
    payload = event.model_dump()
    _save_atomically(payload)
    _latest = payload
    return {"accepted": True, "state": event.state, "event_id": event.event_id}


@app.get("/api/drive-state")
def current_drive_state(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    if _latest is not None:
        return {"available": True, "event": _latest}
    if STATE_FILE.is_file():
        return {"available": True, "event": json.loads(STATE_FILE.read_text(encoding="utf-8"))}
    return {"available": False, "event": None}


@app.get("/api/capture-mode")
def capture_mode_status(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    return capture_worker.status()


@app.post("/api/capture-mode")
def set_capture_mode(
    request: CaptureModeRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _authorise(authorization)
    return capture_worker.set_enabled(request.enabled)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("VISION_DRIVE_STATE_PORT", "8002")))
