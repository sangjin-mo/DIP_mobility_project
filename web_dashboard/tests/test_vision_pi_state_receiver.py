from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from web_dashboard import vision_pi_state_receiver as receiver


def test_receiver_saves_and_returns_latest_state(tmp_path, monkeypatch):
    state_file = tmp_path / "drive-state.json"
    monkeypatch.setattr(receiver, "STATE_FILE", state_file)
    monkeypatch.setattr(receiver, "SHARED_TOKEN", "secret")
    monkeypatch.setattr(receiver, "_latest", None)
    event = {
        "event_id": "event-1",
        "state": "RUNNING",
        "previous_state": "STOPPED",
        "target_speed_mps": 0.25,
        "changed_at_ms": 123456789,
        "source": "web_dashboard",
    }

    with TestClient(receiver.app) as client:
        unauthorised = client.post("/api/drive-state", json=event)
        accepted = client.post(
            "/api/drive-state",
            json=event,
            headers={"Authorization": "Bearer secret"},
        )
        latest = client.get(
            "/api/drive-state",
            headers={"Authorization": "Bearer secret"},
        )

    assert unauthorised.status_code == 401
    assert accepted.json()["accepted"] is True
    assert latest.json()["event"]["state"] == "RUNNING"
    assert state_file.is_file()


def test_capture_mode_endpoint_controls_worker(monkeypatch):
    monkeypatch.setattr(receiver, "SHARED_TOKEN", None)
    status = {
        "enabled": True,
        "saved_count": 0,
        "last_filename": None,
        "last_error": None,
        "capture_dir": "/tmp/images",
        "interval_s": 1.0,
    }
    set_enabled = MagicMock(return_value=status)
    monkeypatch.setattr(receiver.capture_worker, "set_enabled", set_enabled)
    monkeypatch.setattr(receiver.capture_worker, "status", MagicMock(return_value=status))

    with TestClient(receiver.app) as client:
        changed = client.post("/api/capture-mode", json={"enabled": True})
        current = client.get("/api/capture-mode")

    assert changed.json()["enabled"] is True
    assert current.json()["enabled"] is True
    set_enabled.assert_called_once_with(True)


def test_capture_worker_writes_jpeg_to_local_day_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(receiver, "CAPTURE_DIR", tmp_path)

    class Buffer:
        def tobytes(self):
            return b"jpeg-data"

    fake_cv2 = MagicMock()
    fake_cv2.imencode.return_value = (True, Buffer())
    now = datetime(2026, 8, 23, 15, 30, 45)

    relative_path = receiver.CaptureWorker._write_frame(fake_cv2, object(), now, 1)

    assert Path(relative_path).parts == ("2026-08-23", "20260823_153045_cam01_001.jpg")
    assert (tmp_path / "2026-08-23" / "20260823_153045_cam01_001.jpg").read_bytes() == b"jpeg-data"
