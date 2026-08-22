"""Dashboard-only configuration.

AI-owned paths and ports continue to come from ``ai_report.config.Settings``.
Only values that belong exclusively to the WEB process are declared here.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DASHBOARD_",
        env_ignore_empty=True,
        extra="ignore",
    )

    HOST: str = "0.0.0.0"
    PORT: int = 8080
    LIVE_POLL_INTERVAL_S: float = 1.0
    TELEMETRY_STALE_AFTER_S: float = 3.0
    CAMERA_URL: str | None = None

    # Existing VIS PC server. The dashboard calls its transfer endpoint and
    # proxies the newest uploaded still image; it never touches webcam GPIO.
    VISION_SERVER_URL: str | None = None
    VISION_TIMEOUT_S: float = Field(default=35.0, gt=0, le=60)
    VISION_MAX_IMAGE_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)

    # Raspberry Pi control agent. This must point at the agent's command
    # endpoint, for example http://192.168.0.42:9200/api/control.
    ROVER_CONTROL_URL: str | None = None
    # Optional override. By default /api/status is derived from CONTROL_URL.
    ROVER_STATUS_URL: str | None = None
    ROVER_CONTROL_TOKEN: str | None = None
    CONTROL_TIMEOUT_S: float = Field(default=2.0, gt=0, le=10)
    DEFAULT_TARGET_SPEED_MPS: float = Field(default=0.25, gt=0, le=1.0)
    MAX_TARGET_SPEED_MPS: float = Field(default=0.50, gt=0, le=1.0)

    # KMA Village Forecast API. Both encoded and decoded data.go.kr keys are
    # accepted. A fixed farm only needs its grid coordinates configured once;
    # the browser never receives the service key.
    KMA_SERVICE_KEY: str | None = None
    KMA_NX: int | None = Field(default=None, gt=0)
    KMA_NY: int | None = Field(default=None, gt=0)
    WEATHER_LOCATION_LABEL: str = "대구광역시 수성구"
    WEATHER_REFRESH_INTERVAL_MINUTES: int = Field(default=30, ge=10, le=60)
    WEATHER_TIMEOUT_S: float = Field(default=5.0, gt=0, le=20)


def get_dashboard_settings() -> DashboardSettings:
    return DashboardSettings()
