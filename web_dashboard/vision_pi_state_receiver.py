"""Standalone drive-state receiver for the webcam Raspberry Pi.

This WEB-owned process runs beside the vision team's capture.py. It does not
open the camera; capture.py keeps exclusive camera ownership on port 8002.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

STATE_FILE = Path(os.getenv("VISION_DRIVE_STATE_FILE", "vision_drive_state.json"))
SHARED_TOKEN = os.getenv("VISION_DRIVE_STATE_TOKEN")


class DriveStateEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    state: str = Field(pattern="^(RUNNING|STOPPED|EMERGENCY)$")
    previous_state: str | None = Field(default=None, pattern="^(RUNNING|STOPPED|EMERGENCY)$")
    target_speed_mps: float | None = Field(default=None, ge=0, le=1.0)
    changed_at_ms: int = Field(gt=0)
    source: str = "web_dashboard"


app = FastAPI(title="Webcam Pi Drive State Receiver")
_latest: dict | None = None


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


if __name__ == "__main__":
    # Port 8002 belongs to vision/image_transfer/.../capture.py.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("VISION_DRIVE_STATE_PORT", "8003")))
