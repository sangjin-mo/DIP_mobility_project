"""Korea Meteorological Administration weather adapter with a TTL cache."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))
KMA_BASE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"


class WeatherUnavailableError(ConnectionError):
    pass


class WeatherDataError(RuntimeError):
    pass


class KmaWeatherService:
    """Fetch current observations plus a sky condition from the KMA API."""

    def __init__(
        self,
        service_key: str | None,
        nx: int | None,
        ny: int | None,
        *,
        location_label: str = "",
        refresh_interval_minutes: int = 30,
        timeout_s: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        # data.go.kr shows both encoded and decoded keys. Normalising once lets
        # urllib encode the query exactly once in either case.
        self._service_key = urllib.parse.unquote(service_key.strip()) if service_key else None
        self._nx = nx
        self._ny = ny
        self._location_label = location_label.strip()
        self._cache_ttl = timedelta(minutes=refresh_interval_minutes)
        self._timeout_s = timeout_s
        self._now = now or (lambda: datetime.now(KST))
        self._cache: dict[str, Any] | None = None
        self._cache_expires_at: datetime | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._service_key and self._nx is not None and self._ny is not None)

    def get(self, force_refresh: bool = False) -> dict[str, Any]:
        if not self.configured:
            raise WeatherUnavailableError(
                "기상청 API가 설정되지 않았습니다. DASHBOARD_KMA_SERVICE_KEY, "
                "DASHBOARD_KMA_NX, DASHBOARD_KMA_NY를 설정하세요."
            )

        with self._lock:
            now = self._now().astimezone(KST)
            if (
                not force_refresh
                and self._cache is not None
                and self._cache_expires_at is not None
                and now < self._cache_expires_at
            ):
                return dict(self._cache)

            try:
                observation_base = _observation_base(now)
                forecast_base = _forecast_base(now)
                observations = self._request("getUltraSrtNcst", observation_base)
                forecasts = self._request("getUltraSrtFcst", forecast_base)
                probability_forecasts: list[dict[str, Any]] = []
                try:
                    probability_forecasts = self._request(
                        "getVilageFcst", _village_forecast_base(now)
                    )
                except (WeatherDataError, urllib.error.URLError, TimeoutError, OSError):
                    # POP belongs to the village forecast endpoint. A temporary
                    # failure there must not hide otherwise valid observations.
                    pass
                hourly_history = self._hourly_history(observation_base, observations)
                result = self._normalise(
                    observations,
                    forecasts,
                    probability_forecasts,
                    hourly_history,
                    observation_base,
                    now,
                )
            except (WeatherDataError, urllib.error.URLError, TimeoutError, OSError) as exc:
                if self._cache is not None:
                    stale = dict(self._cache)
                    stale["is_stale"] = True
                    stale["error"] = str(exc)
                    return stale
                raise WeatherUnavailableError(f"기상청 API를 조회할 수 없습니다: {exc}") from exc

            self._cache = result
            self._cache_expires_at = now + self._cache_ttl
            return dict(result)

    def _request(self, endpoint: str, base: datetime) -> list[dict[str, Any]]:
        params = {
            "ServiceKey": self._service_key,
            "pageNo": 1,
            "numOfRows": 1000,
            "dataType": "JSON",
            "base_date": base.strftime("%Y%m%d"),
            "base_time": base.strftime("%H%M"),
            "nx": self._nx,
            "ny": self._ny,
        }
        url = f"{KMA_BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"

        try:
            with urllib.request.urlopen(url, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WeatherDataError(f"기상청 HTTP {exc.code}: {detail[:200]}") from exc

        try:
            payload = json.loads(raw)
            api_response = payload["response"]
            header = api_response["header"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise WeatherDataError("기상청 API가 올바른 JSON을 반환하지 않았습니다.") from exc

        if str(header.get("resultCode")) != "00":
            message = header.get("resultMsg", "알 수 없는 오류")
            raise WeatherDataError(f"기상청 API 오류: {message}")

        items = api_response.get("body", {}).get("items", {})
        if not isinstance(items, dict) or not isinstance(items.get("item"), list):
            raise WeatherDataError("요청한 시각과 위치에 기상청 데이터가 없습니다.")
        return items["item"]

    def _normalise(
        self,
        observations: list[dict[str, Any]],
        forecasts: list[dict[str, Any]],
        probability_forecasts: list[dict[str, Any]],
        hourly_history: list[dict[str, Any]],
        observation_base: datetime,
        fetched_at: datetime,
    ) -> dict[str, Any]:
        values = {
            str(item.get("category")): item.get("obsrValue")
            for item in observations
            if item.get("category")
        }
        forecast = _nearest_forecast(forecasts, fetched_at)
        probability_forecast = _nearest_forecast(
            probability_forecasts, fetched_at, categories={"POP"}
        )

        temperature = _as_float(values.get("T1H"))
        humidity = _as_float(values.get("REH"))
        precipitation = _as_float(values.get("RN1"))
        wind_speed = _as_float(values.get("WSD"))
        precipitation_code = _as_int(values.get("PTY"))
        if precipitation_code is None:
            precipitation_code = _as_int(forecast.get("PTY"))

        sky_code = _as_int(forecast.get("SKY"))
        precipitation_label = _precipitation_label(precipitation_code)
        sky_label = _sky_label(sky_code)
        weather = precipitation_label if precipitation_code not in (None, 0) else sky_label
        is_raining = (precipitation_code not in (None, 0)) or (
            precipitation is not None and precipitation > 0
        )

        return {
            "configured": True,
            "temperature_c": temperature,
            "humidity_percent": humidity,
            "weather": weather,
            "weather_icon": _weather_icon(precipitation_code, sky_code),
            "precipitation_type": precipitation_label,
            "is_raining": is_raining,
            "precipitation_mm": precipitation,
            "rain_probability_percent": _as_float(probability_forecast.get("POP")),
            "hourly_history": hourly_history,
            "wind_speed_mps": wind_speed,
            "observed_at": observation_base.isoformat(),
            "fetched_at": fetched_at.isoformat(),
            "source": "기상청 초단기실황·초단기예보·단기예보",
            "location": self._location_label,
            "grid": {"nx": self._nx, "ny": self._ny},
            "is_stale": False,
        }

    def _hourly_history(
        self,
        latest_at: datetime,
        latest_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fetch and normalise hourly observations from 23 hours ago to now."""
        slots = [latest_at - timedelta(hours=offset) for offset in range(23, -1, -1)]
        observations_by_time = {latest_at: latest_items}
        previous_slots = slots[:-1]

        # The endpoint accepts one base time per request. Parallel requests
        # keep the initial dashboard load bounded by a few request timeouts.
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self._request, "getUltraSrtNcst", slot): slot
                for slot in previous_slots
            }
            for future in as_completed(futures):
                slot = futures[future]
                try:
                    observations_by_time[slot] = future.result()
                except (WeatherDataError, urllib.error.URLError, TimeoutError, OSError):
                    continue

        return [
            _normalise_history_point(slot, observations_by_time[slot])
            for slot in slots
            if slot in observations_by_time
        ]


def _observation_base(now: datetime) -> datetime:
    # Ultra-short observations are issued hourly. Keep a publication delay so
    # a request just after the hour does not ask for a not-yet-published slot.
    return (now - timedelta(minutes=15)).replace(minute=0, second=0, microsecond=0)


def _forecast_base(now: datetime) -> datetime:
    # Ultra-short forecasts use HH30 base times and are published after that.
    return (now - timedelta(minutes=45)).replace(minute=30, second=0, microsecond=0)


def _village_forecast_base(now: datetime) -> datetime:
    # Village forecasts are issued at 02, 05, 08, 11, 14, 17, 20 and 23 KST.
    # Allow ten minutes for publication before selecting the newest base slot.
    available_at = now - timedelta(minutes=10)
    slots = (2, 5, 8, 11, 14, 17, 20, 23)
    available_slots = [hour for hour in slots if hour <= available_at.hour]
    if available_slots:
        return available_at.replace(hour=max(available_slots), minute=0, second=0, microsecond=0)
    previous_day = available_at - timedelta(days=1)
    return previous_day.replace(hour=23, minute=0, second=0, microsecond=0)


def _nearest_forecast(
    items: list[dict[str, Any]],
    now: datetime,
    *,
    categories: set[str] | None = None,
) -> dict[str, Any]:
    selected_categories = categories or {"SKY", "PTY"}
    grouped: dict[datetime, dict[str, Any]] = {}
    for item in items:
        category = str(item.get("category", ""))
        if category not in selected_categories:
            continue
        try:
            forecast_at = datetime.strptime(
                f"{item['fcstDate']}{item['fcstTime']}", "%Y%m%d%H%M"
            ).replace(tzinfo=KST)
        except (KeyError, TypeError, ValueError):
            continue
        grouped.setdefault(forecast_at, {})[category] = item.get("fcstValue")

    if not grouped:
        return {}
    future = [timestamp for timestamp in grouped if timestamp >= now]
    selected = min(future) if future else max(grouped)
    return grouped[selected]


def _normalise_history_point(
    observed_at: datetime, items: list[dict[str, Any]]
) -> dict[str, Any]:
    values = {
        str(item.get("category")): item.get("obsrValue")
        for item in items
        if item.get("category")
    }
    precipitation_raw = values.get("RN1")
    return {
        "observed_at": observed_at.isoformat(),
        "temperature_c": _as_float(values.get("T1H")),
        "precipitation_mm": _observed_precipitation_mm(precipitation_raw),
        "precipitation_label": precipitation_raw,
    }


def _observed_precipitation_mm(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is not None:
        return numeric
    if value is None:
        return None
    text = str(value).strip()
    if text in {"강수없음", "없음"}:
        return 0.0
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    return float(numbers[0]) if numbers else None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _precipitation_label(code: int | None) -> str:
    return {
        0: "없음",
        1: "비",
        2: "비/눈",
        3: "눈",
        5: "빗방울",
        6: "빗방울/눈날림",
        7: "눈날림",
    }.get(code, "확인 불가")


def _sky_label(code: int | None) -> str:
    return {1: "맑음", 3: "구름 많음", 4: "흐림"}.get(code, "확인 불가")


def _weather_icon(precipitation_code: int | None, sky_code: int | None) -> str:
    if precipitation_code in {1, 5}:
        return "rain"
    if precipitation_code in {2, 6}:
        return "sleet"
    if precipitation_code in {3, 7}:
        return "snow"
    return {1: "sunny", 3: "partly_cloudy", 4: "cloudy"}.get(sky_code, "unknown")
