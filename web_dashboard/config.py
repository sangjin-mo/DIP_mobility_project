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
        extra="ignore",
    )

    HOST: str = "0.0.0.0"
    PORT: int = 8080
    LIVE_POLL_INTERVAL_S: float = 1.0
    TELEMETRY_STALE_AFTER_S: float = 3.0
    CAMERA_URL: str | None = None

    # Raspberry Pi control agent. This must point at the agent's command
    # endpoint, for example http://192.168.0.42:9200/api/control.
    ROVER_CONTROL_URL: str | None = None
    ROVER_CONTROL_TOKEN: str | None = None
    CONTROL_TIMEOUT_S: float = Field(default=2.0, gt=0, le=10)
    DEFAULT_TARGET_SPEED_MPS: float = Field(default=0.25, gt=0, le=1.0)


def get_dashboard_settings() -> DashboardSettings:
    return DashboardSettings()
