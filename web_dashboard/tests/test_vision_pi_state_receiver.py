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
