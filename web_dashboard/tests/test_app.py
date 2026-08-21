import time
from unittest.mock import patch

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


def test_dashboard_four_section_layout_keeps_existing_feature_hooks(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(CAMERA_URL=None),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "스마트 농장 관리 대시보드" in html
    for element_id in (
        "capture-image",
        "start-drive",
        "stop-drive",
        "weather-temperature",
        "rain-advice",
        "temperature-advice",
        "weather-fetched-at",
        "report-list",
        "report-content",
    ):
        assert f'id="{element_id}"' in html
    assert "순찰 구역 맵" not in html
    assert "현재 속도" not in html
    assert "목표 속도" not in html
    assert "농작물 00에 급수를 공급하세요" in html
    assert "우산을 챙기세요" in html
    assert "온열 질환에 유의하세요" in html
    assert "야외 활동을 자제하세요" in html
    assert "실제 분석값이 들어오기 전에는 임의 상태를 표시하지 않습니다." in html


def test_control_api_is_disabled_until_rover_url_is_configured(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(ROVER_CONTROL_URL=None),
    )

    with TestClient(app) as client:
        assert client.get("/api/status").json()["control_configured"] is False
        response = client.post("/api/control/stop")

    assert response.status_code == 503


def test_control_api_forwards_validated_commands(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(
            ROVER_CONTROL_URL="http://rover.local:9200/api/control",
            DEFAULT_TARGET_SPEED_MPS=0.2,
        ),
    )
    accepted = {
        "accepted": True,
        "command": "START",
        "command_id": "test-id",
        "target_speed_mps": 0.3,
        "rover": {"accepted": True, "state": "RUNNING"},
    }

    with (
        patch("web_dashboard.app.RoverControlService.send", return_value=accepted) as send,
        TestClient(app) as client,
    ):
        assert client.get("/api/status").json()["control_configured"] is True
        response = client.post("/api/control/start", json={"target_speed_mps": 0.3})

    assert response.status_code == 200
    assert response.json()["rover"]["state"] == "RUNNING"
    assert send.call_args.args[1:] == (0.3,)


def test_control_api_uses_default_speed_for_button_only_start(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(
            ROVER_CONTROL_URL="http://rover.local:9200/api/control",
            DEFAULT_TARGET_SPEED_MPS=0.25,
        ),
    )
    accepted = {"accepted": True, "command": "START", "rover": {"state": "RUNNING"}}

    with (
        patch("web_dashboard.app.RoverControlService.send", return_value=accepted) as send,
        TestClient(app) as client,
    ):
        response = client.post("/api/control/start", json={})

    assert response.status_code == 200
    assert send.call_args.args[1:] == (0.25,)


def test_weather_api_is_disabled_until_kma_settings_are_configured(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(KMA_SERVICE_KEY=None, KMA_NX=None, KMA_NY=None),
    )

    with TestClient(app) as client:
        status = client.get("/api/status").json()
        response = client.get("/api/weather")

    assert status["weather_configured"] is False
    assert response.status_code == 503
    assert "DASHBOARD_KMA_SERVICE_KEY" in response.json()["detail"]


def test_weather_api_returns_normalised_kma_data(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(KMA_SERVICE_KEY="key", KMA_NX=60, KMA_NY=127),
    )
    weather = {
        "configured": True,
        "temperature_c": 27.3,
        "humidity_percent": 68,
        "weather": "맑음",
        "weather_icon": "sunny",
        "is_raining": False,
        "precipitation_mm": 0,
        "wind_speed_mps": 2.1,
        "observed_at": "2026-08-20T14:00:00+09:00",
        "fetched_at": "2026-08-20T14:20:00+09:00",
        "source": "기상청 초단기실황/예보",
        "grid": {"nx": 60, "ny": 127},
        "is_stale": False,
    }

    with (
        patch("web_dashboard.app.KmaWeatherService.get", return_value=weather),
        TestClient(app) as client,
    ):
        status = client.get("/api/status").json()
        response = client.get("/api/weather")

    assert status["weather_configured"] is True
    assert status["weather_refresh_interval_s"] == 1800
    assert response.status_code == 200
    assert response.json()["temperature_c"] == 27.3


def test_weather_refresh_api_bypasses_server_cache(tmp_path):
    app = create_app(
        ai_settings=Settings(
            DATA_ROOT=tmp_path / "data",
            REPORT_ROOT=tmp_path / "reports",
            LLM_ENABLED=False,
        ),
        dashboard_settings=DashboardSettings(KMA_SERVICE_KEY="key", KMA_NX=89, KMA_NY=90),
    )
    weather = {"temperature_c": 28.0, "weather": "맑음", "weather_icon": "sunny"}

    with (
        patch("web_dashboard.app.KmaWeatherService.get", return_value=weather) as get_weather,
        TestClient(app) as client,
    ):
        response = client.post("/api/weather/refresh")

    assert response.status_code == 200
    get_weather.assert_called_once_with(True)
