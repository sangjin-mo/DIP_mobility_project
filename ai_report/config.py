"""Central configuration. No threshold appears inline anywhere else in this package.

Called by: every other module in `ai_report` that needs a port number, a
filesystem root, or a threshold — `cli.py`, `ingest/udp_listener.py` (via
`cli.py`), `devtools/fake_rover.py`, `devtools/fake_vis.py`. Each of those
calls `get_settings()` rather than instantiating `Settings` directly, so
there is exactly one way to obtain configuration in the codebase.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunable values for the AI subsystem, loaded from `.env` (and process
    environment variables, which pydantic-settings reads automatically).

    Fields are grouped by the pipeline stage that consumes them. The
    "Pipeline" and "LLM" groups match the surface described in
    `02-ai-subsystem-spec.md` §3. The "Segmentation fallback" group does
    not appear in that spec at all — see the `[!FLAG]` in
    `02-ai-subsystem-spec.md` §5 explaining why `pipeline/segment.py`'s
    distance-based fallback path needed config the docs never defined.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Network — ingest (C1.1, C1.2)
    UDP_PORT: int = 9100
    EVENT_PORT: int = 9101
    UDP_MAX_PACKET_BYTES: int = 1400
    UDP_RECV_BUFFER_BYTES: int = 2048
    EVENT_UDP_FALLBACK_RESENDS: int = 3

    # Storage
    DATA_ROOT: Path = Path("./data")
    REPORT_ROOT: Path = Path("./reports")

    # Ingest behaviour.
    # VIS_COMPLETE_TIMEOUT_S is a give-up ceiling, not a delay: `_COMPLETE` is
    # written by `vision/image_analysis/system/classify.py` the moment it
    # finishes (ADR-0010), so the watcher returns then and the ceiling only
    # applies when classify never ran or died before touching the marker.
    # Cut from 600 to 120 for demo runs -- with a short track the interesting
    # question is "how fast do I find out it broke", not "how long can VIS
    # take". Override in .env if a patrol's classification legitimately runs
    # longer (classify.py is sequential: roughly 2-6s per captured image).
    VIS_COMPLETE_TIMEOUT_S: int = 120
    VIS_WATCHER_POLL_INTERVAL_S: float = 1.0

    # Pipeline (used from A2 onward; declared now so the surface matches spec §3)
    IMAGES_PER_ZONE_MAX: int = 3
    IMAGE_QUALITY_MIN: float = 0.40
    IMAGE_RESIZE_PX: int = 768
    IMAGE_JPEG_QUALITY: int = 85
    UNDETERMINED_FLAG_THRESHOLD: float = 0.30
    COVERAGE_WARN_THRESHOLD: float = 0.90
    TREND_MIN_PATROLS: int = 10

    # Token budget estimate (spec §8's table) — a rough per-component
    # heuristic, not real tokenization (no tokenizer dependency exists in
    # this package; A5's OpenAI SDK may report real counts after the call,
    # but pipeline/payload.py must estimate *before* calling).
    TOKEN_ESTIMATE_PER_ZONE: int = 200
    TOKEN_ESTIMATE_FIXED: int = 300
    TOKEN_ESTIMATE_SYSTEM_PROMPT: int = 700
    TOKEN_ESTIMATE_PER_IMAGE: int = 765

    # Segmentation fallback (pipeline/segment.py, spec §5's fallback path) —
    # not specified anywhere in the docs; see the [!FLAG] in
    # 02-ai-subsystem-spec.md §5. Only used when no ZONE_ENTER events exist.
    ROUTE_ZONE_COUNT: int = 6
    ROUTE_TOTAL_DISTANCE_M: float = 120.0

    # zone_id -> Korean display name (e.g. "B동 2열"), for metadata.json's
    # zones[].zone_name (ICD §C3.3). No document defines where this comes
    # from — see the same [!FLAG]. Falls back to "{zone_id}구역" when a zone
    # isn't in this map, so an unconfigured route still produces valid output.
    ZONE_NAMES: dict[int, str] = Field(default_factory=dict)

    # crop class (AnalysisResult.detections[].class, lowercase ASCII per
    # C2.2) -> Korean display name, for a crop-type zone's zone_name
    # (ADR-0009: pipeline/aggregate.py::aggregate_zones_by_crop_type).
    # Falls back to the raw class string when a class isn't in this map, so
    # an unrecognised crop still produces a valid (if less pretty) zone name.
    CROP_DISPLAY_NAMES: dict[str, str] = Field(
        default_factory=lambda: {"tomato": "토마토", "chili_pepper": "고추"}
    )

    # LLM (used from A5 onward)
    LLM_ENABLED: bool = True
    LLM_MODEL: str = "gpt-5.6-luna"
    LLM_MAX_RETRIES: int = 3
    LLM_TIMEOUT_S: int = 120
    LLM_MAX_INPUT_TOKENS: int = 60000
    PROMPT_VERSION: str = "v1.1"
    OPENAI_API_KEY: str | None = None  # env only; never logged, never committed

    # Retry backoff (spec §9: "3 attempts, exponential backoff 2/4/8 s") —
    # 3 retries beyond the first attempt (4 attempts total), backoff before
    # retry i is LLM_RETRY_BACKOFF_BASE_S * 2**(i-1): 2s, 4s, 8s.
    LLM_RETRY_BACKOFF_BASE_S: float = 2.0

    # Cost metering (spec §8: "gpt-5.6-luna rates ($0.20 / $1.20 per 1M)").
    LLM_INPUT_COST_PER_1M_USD: float = 0.20
    LLM_OUTPUT_COST_PER_1M_USD: float = 1.20

    @property
    def sqlite_path(self) -> Path:
        """Where `ingest/store.py::Store` opens its SQLite file: `DATA_ROOT/sessions.db`.

        A property (not a stored field) so it always tracks `DATA_ROOT`,
        including a `DATA_ROOT` overridden via environment variable or `.env`.
        Called by: `cli.py::_serve` when constructing the shared `Store`.
        """
        return self.DATA_ROOT / "sessions.db"


def get_settings() -> Settings:
    """Build a fresh `Settings` instance, re-reading `.env`/the environment.

    No caching: A1 is a short-lived batch/service process, so re-parsing on
    each call is cheap and avoids stale-settings bugs in tests that mutate
    `os.environ`. Called by: `cli.py::_serve`, `devtools/fake_rover.py::main`,
    `devtools/fake_vis.py::main`.
    """
    return Settings()
