import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from web_dashboard.services.weather_service import KST, KmaWeatherService, WeatherUnavailableError


class FakeResponse:
    def __init__(self, items: list[dict]) -> None:
        self._body = json.dumps(
            {
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {"items": {"item": items}},
                }
            }
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def observation_items() -> list[dict]:
    return [
        {"category": "T1H", "obsrValue": "27.3"},
        {"category": "REH", "obsrValue": "68"},
        {"category": "RN1", "obsrValue": "0.8"},
        {"category": "PTY", "obsrValue": "1"},
        {"category": "WSD", "obsrValue": "2.1"},
    ]


def forecast_items() -> list[dict]:
    return [
        {"category": "SKY", "fcstDate": "20260820", "fcstTime": "1500", "fcstValue": "4"},
        {"category": "PTY", "fcstDate": "20260820", "fcstTime": "1500", "fcstValue": "0"},
    ]


def village_forecast_items() -> list[dict]:
    return [
        {"category": "POP", "fcstDate": "20260820", "fcstTime": "1500", "fcstValue": "60"},
        {"category": "POP", "fcstDate": "20260820", "fcstTime": "1600", "fcstValue": "70"},
    ]


def test_unconfigured_weather_service_is_unavailable():
    service = KmaWeatherService(None, None, None)

    with pytest.raises(WeatherUnavailableError, match="DASHBOARD_KMA_SERVICE_KEY"):
        service.get()


def test_fetches_observation_and_forecast_then_normalises_dashboard_shape():
    now = datetime(2026, 8, 20, 14, 20, tzinfo=KST)
    service = KmaWeatherService(
        "decoded key", 60, 127, location_label="대구광역시 수성구", now=lambda: now
    )

    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            FakeResponse(village_forecast_items()),
        ],
    ) as urlopen:
        result = service.get()

    observation_query = urllib.parse.parse_qs(urllib.parse.urlparse(urlopen.call_args_list[0].args[0]).query)
    forecast_query = urllib.parse.parse_qs(urllib.parse.urlparse(urlopen.call_args_list[1].args[0]).query)
    probability_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(urlopen.call_args_list[2].args[0]).query
    )
    assert observation_query["base_date"] == ["20260820"]
    assert observation_query["base_time"] == ["1400"]
    assert forecast_query["base_time"] == ["1330"]
    assert probability_query["base_time"] == ["1400"]
    assert "getVilageFcst" in urlopen.call_args_list[2].args[0]
    assert observation_query["ServiceKey"] == ["decoded key"]
    assert result["temperature_c"] == 27.3
    assert result["humidity_percent"] == 68.0
    assert result["precipitation_mm"] == 0.8
    assert result["rain_probability_percent"] == 60.0
    assert result["wind_speed_mps"] == 2.1
    assert result["weather"] == "비"
    assert result["weather_icon"] == "rain"
    assert result["is_raining"] is True
    assert result["grid"] == {"nx": 60, "ny": 127}
    assert result["location"] == "대구광역시 수성구"
    assert result["is_stale"] is False


def test_encoded_service_key_is_not_double_encoded():
    now = datetime(2026, 8, 20, 14, 20, tzinfo=KST)
    service = KmaWeatherService("abc%2B123%3D", 89, 90, now=lambda: now)

    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            FakeResponse(village_forecast_items()),
        ],
    ) as urlopen:
        service.get()

    query = urllib.parse.parse_qs(urllib.parse.urlparse(urlopen.call_args_list[0].args[0]).query)
    assert query["ServiceKey"] == ["abc+123="]


def test_cache_prevents_repeated_external_calls():
    now = datetime(2026, 8, 20, 14, 20, tzinfo=KST)
    service = KmaWeatherService("key", 60, 127, now=lambda: now)

    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            FakeResponse(village_forecast_items()),
        ],
    ) as urlopen:
        first = service.get()
        second = service.get()

    assert first == second
    assert urlopen.call_count == 3


def test_force_refresh_bypasses_cache():
    now = datetime(2026, 8, 20, 14, 20, tzinfo=KST)
    service = KmaWeatherService("key", 60, 127, now=lambda: now)

    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            FakeResponse(village_forecast_items()),
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            FakeResponse(village_forecast_items()),
        ],
    ) as urlopen:
        service.get()
        service.get(force_refresh=True)

    assert urlopen.call_count == 6


def test_returns_stale_cache_when_kma_is_temporarily_unavailable():
    clock = [datetime(2026, 8, 20, 14, 20, tzinfo=KST)]
    service = KmaWeatherService(
        "key",
        60,
        127,
        refresh_interval_minutes=10,
        now=lambda: clock[0],
    )

    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            FakeResponse(village_forecast_items()),
        ],
    ):
        service.get()

    clock[0] += timedelta(minutes=11)
    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=urllib.error.URLError("offline"),
    ):
        stale = service.get()

    assert stale["is_stale"] is True
    assert "offline" in stale["error"]


def test_probability_failure_keeps_current_weather_available():
    now = datetime(2026, 8, 20, 14, 20, tzinfo=KST)
    service = KmaWeatherService("key", 60, 127, now=lambda: now)

    with patch(
        "web_dashboard.services.weather_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse(observation_items()),
            FakeResponse(forecast_items()),
            urllib.error.URLError("forecast offline"),
        ],
    ):
        result = service.get()

    assert result["temperature_c"] == 27.3
    assert result["rain_probability_percent"] is None
