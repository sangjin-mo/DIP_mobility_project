import time

from fastapi.testclient import TestClient

from ai_report.config import Settings
from ai_report.ingest.store import Store
from ai_report.models import DriveState, TelemetryPacket
from web_dashboard.app import create_app
from web_dashboard.config import DashboardSettings


def test_dashboard_and_live_socket_reuse_existing_store(tmp_path):
    data_root = tmp_path / "data"
    report_root = tmp_path / "reports"
    ai_settings = Settings(DATA_ROOT=data_root, REPORT_ROOT=report_root, LLM_ENABLED=False)
    store = Store(ai_settings.sqlite_path)
    store.insert_telemetry(
        TelemetryPacket(
            patrol_id="20260818_1430",
            seq=1,
            ts_ms=int(time.time() * 1000),
            type="TELEMETRY",
            zone_id=2,
            env={"temp_c": 25.0, "humid_pct": 60.0},
            drive={"speed_mps": 0.2, "steer": 0.1, "ultra_cm": 50, "state": DriveState.RUNNING},
        )
    )
    store.close()

    app = create_app(
        ai_settings=ai_settings,
        dashboard_settings=DashboardSettings(CAMERA_URL=None),
    )

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").json()["database_exists"] is True
        assert client.get("/api/patrols").json() == []
        with client.websocket_connect("/ws/live") as socket:
            snapshot = socket.receive_json()

    assert snapshot["connected"] is True
    assert snapshot["telemetry"]["zone_id"] == 2
