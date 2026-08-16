from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ai_report.ingest.event_api import create_app
from ai_report.ingest.store import Store

PATROL_ID = "20260813_1430"


def _client(tmp_path: Path) -> tuple[TestClient, Store]:
    store = Store(tmp_path / "sessions.db")
    return TestClient(create_app(store)), store


def test_post_event_accepted(tmp_path):
    client, store = _client(tmp_path)
    body = {"patrol_id": PATROL_ID, "event_seq": 0, "ts_ms": 0, "type": "PATROL_START", "detail": {}}
    resp = client.post("/api/events", json=body)
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted", "duplicate": False}
    store.close()


def test_post_event_idempotent(tmp_path):
    client, store = _client(tmp_path)
    body = {"patrol_id": PATROL_ID, "event_seq": 7, "ts_ms": 12000, "type": "ZONE_ENTER", "zone_id": 4}
    r1 = client.post("/api/events", json=body)
    r2 = client.post("/api/events", json=body)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert len(store.events_for_patrol(PATROL_ID)) == 1
    store.close()


def test_post_event_malformed_rejected(tmp_path):
    client, store = _client(tmp_path)
    body = {"patrol_id": "not-a-patrol-id", "event_seq": 0, "ts_ms": 0, "type": "NOT_A_REAL_TYPE"}
    resp = client.post("/api/events", json=body)
    assert resp.status_code == 422
    assert store.events_for_patrol(PATROL_ID) == []
    store.close()
