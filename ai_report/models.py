"""Pydantic models for every schema at a subsystem boundary.

Source-of-truth order per CLAUDE.md: `contracts/schemas/` beats
`docs/01-interface-contracts.md` beats everything else.

All four boundary schemas now exist on disk under `contracts/schemas/`
(`c1-telemetry`, `c1-event`, `c2-analysis`, `c3-metadata` — DR, VIS, and WEB
agreed 2026-08-13 to implement against these as written; see
`01-interface-contracts.md`'s "Contract status" section). Every model below
is written to match its schema field-for-field, but is still hand-written
rather than `datamodel-codegen`-generated: `Detection`'s
state-dependent-nullable `confidence` (rule C2.3) and the `patrol_id`
format are validated here with logic JSON Schema alone can't fully express
as a single cross-field constraint. `contracts/validate.py` plus a manual
schema round-trip (see `01-interface-contracts.md`'s `[!FLAG]`) are what
catch drift between this file and the schemas instead.

`PatrolAggregate` (bottom of this file) mirrors `c3-metadata.schema.json`
and is what `pipeline/aggregate.py::aggregate()` (A2) returns and
`storage/layout.py` (A3) writes as `metadata.json`. It intentionally
excludes fields the schema doesn't expose to WEB (per-zone dwell time,
image count, raw drive-event list) — those are computed as local values
inside `aggregate()` to derive `status`/`flags`, not persisted on the model.
`zones[].image_ids` is always `[]` until A4's image selection exists.

Called by (who constructs/parses these models):
- `ingest/udp_listener.py::TelemetryUDPProtocol._handle` — parses raw UDP
  JSON into `TelemetryPacket` or `EventMessage` via `model_validate`.
- `ingest/event_api.py::post_event` — FastAPI parses the HTTP body into
  `EventMessage` automatically (it's the handler's type-annotated parameter).
- `ingest/vis_watcher.py::VisWatcher.scan_once` — parses analysis JSON files
  into `AnalysisResult` via `model_validate`.
- `ingest/store.py::Store` — reads `.patrol_id`/`.seq`/etc. off these models
  when writing rows, and reconstructs `EventMessage` from stored rows in
  `events_for_patrol`.
- `devtools/fake_rover.py::generate_patrol_plan` — constructs
  `TelemetryPacket`/`EventMessage`/`EnvReading`/`DriveReading` directly.
- `devtools/fake_vis.py::generate_analysis_results` — constructs
  `AnalysisResult`/`Detection` directly.
- `pipeline/aggregate.py::aggregate` — constructs `PatrolAggregate` and its
  nested `ZoneMetadata`/`StatSummary`/`DataCompleteness`/`LlmMetadata`.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PATROL_ID_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{4}$")


class DriveState(str, Enum):
    """Rover motion state carried in `TelemetryPacket.drive.state` (C1.1)."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    EMERGENCY = "EMERGENCY"


class EventType(str, Enum):
    """The five discrete event kinds defined in ICD §C1.2.

    `udp_listener.py` uses `{t.value for t in EventType}` (see
    `_EVENT_TYPE_VALUES`) to decide whether an incoming UDP datagram whose
    `type` isn't `"TELEMETRY"` should be parsed as an `EventMessage`.
    """

    PATROL_START = "PATROL_START"
    ZONE_ENTER = "ZONE_ENTER"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    LINE_LOST = "LINE_LOST"
    PATROL_END = "PATROL_END"


class CropState(str, Enum):
    """Closed set — a fifth value, typo, or translated variant is a contract violation.

    Because this is a `str, Enum`, Pydantic rejects any `state` value
    outside these four automatically when validating a `Detection` — that
    rejection is what makes `ingest/vis_watcher.py::scan_once` raise on an
    unknown VIS state instead of silently coercing it (CLAUDE.md hard rule,
    error-handling matrix in spec §12).
    """

    NORMAL = "정상"
    IMMATURE = "미성숙"
    SUSPECTED_DISEASE = "병충해_의심"
    UNDETERMINED = "판단불가"


class ReportStatus(str, Enum):
    """Per-zone / overall report status. Not produced until A2's status rule
    (spec §6); declared here now so the enum exists wherever it's needed.
    """

    NORMAL = "정상"
    CAUTION = "주의"
    ABNORMAL = "이상"


def _validate_patrol_id(v: str) -> str:
    """Enforce the `YYYYMMDD_HHMM` patrol_id format (ICD §C1.1/§C1.2/§C2.2).

    Bound as a Pydantic field validator on `TelemetryPacket.patrol_id`,
    `EventMessage.patrol_id`, and `AnalysisResult.patrol_id` below — Pydantic
    calls it automatically during `model_validate`/construction of each of
    those three models, so it is never called directly elsewhere.
    """
    if not PATROL_ID_PATTERN.match(v):
        raise ValueError(f"patrol_id must match YYYYMMDD_HHMM, got {v!r}")
    return v


# Shared field type: fixes patrol_id's length at "YYYYMMDD_HHMM" (13 chars).
PatrolId = Annotated[str, Field(min_length=13, max_length=13)]


# --- C1.1 Telemetry (DR -> AI, UDP) --------------------------------------


class EnvReading(BaseModel):
    """Temperature/humidity sample nested in `TelemetryPacket.env`.

    Both fields are nullable: DR reports `null` when the sensor read fails,
    or always when no temp/humidity sensor is fitted at all (see the
    hardware-gap flag in `01-interface-contracts.md` §C1.1). Constructed
    directly by `devtools/fake_rover.py::generate_patrol_plan`; parsed as
    part of `TelemetryPacket.model_validate` in `udp_listener.py`.
    """

    model_config = ConfigDict(extra="forbid")

    # No default: the schema requires both keys present (value may be null),
    # matching c1-telemetry.schema.json's `"required": ["temp_c", "humid_pct"]`.
    temp_c: float | None = Field(ge=-40, le=85)
    humid_pct: float | None = Field(ge=0, le=100)


class DriveReading(BaseModel):
    """Drive/steering telemetry nested in `TelemetryPacket.drive`.

    `ultra_cm` is nullable for the same hardware-gap reason as `EnvReading`.
    Constructed directly by `fake_rover.py::generate_patrol_plan`; parsed as
    part of `TelemetryPacket.model_validate` in `udp_listener.py`.
    """

    model_config = ConfigDict(extra="forbid")

    speed_mps: float = Field(ge=0, le=5)
    steer: float = Field(ge=-1, le=1)
    # No default: required key, nullable value — see EnvReading's note above.
    ultra_cm: int | None = Field(ge=0)
    state: DriveState


class TelemetryPacket(BaseModel):
    """One UDP telemetry datagram (C1.1). Mirrors c1-telemetry.schema.json field for field.

    Validated in `udp_listener.py::TelemetryUDPProtocol._handle` via
    `TelemetryPacket.model_validate(raw)` whenever an incoming datagram's
    `type` is `"TELEMETRY"`. `extra="forbid"` makes an unexpected field a
    validation error rather than a silently ignored one, matching the
    schema's `additionalProperties: false`. Constructed directly (not
    parsed) by `fake_rover.py::generate_patrol_plan`, then serialised back
    to JSON and sent over a real UDP socket by `fake_rover.py::replay`.
    """

    model_config = ConfigDict(extra="forbid")

    patrol_id: PatrolId
    seq: int = Field(ge=0)
    ts_ms: int = Field(ge=0)
    type: Literal["TELEMETRY"]
    # No default: required key, nullable value, per c1-telemetry.schema.json.
    zone_id: int | None = Field(ge=0)
    env: EnvReading
    drive: DriveReading

    _validate_patrol_id = field_validator("patrol_id")(_validate_patrol_id)


# --- C1.2 Events (DR -> AI, HTTP with UDP fallback) ----------------------


class EventMessage(BaseModel):
    """One discrete patrol event (C1.2) — PATROL_START / ZONE_ENTER /
    EMERGENCY_STOP / LINE_LOST / PATROL_END.

    Arrives two ways, both of which validate against this same model:
    - HTTP: as the request body of `POST /api/events`
      (`ingest/event_api.py::post_event`), parsed automatically by FastAPI.
    - UDP fallback: parsed explicitly via `EventMessage.model_validate(raw)`
      in `ingest/udp_listener.py::TelemetryUDPProtocol._handle` whenever the
      datagram's `type` is one of the `EventType` values.

    `ingest/store.py::Store.events_for_patrol` also *constructs* this model
    when reading rows back out of SQLite.
    """

    model_config = ConfigDict(extra="forbid")

    patrol_id: PatrolId
    event_seq: int = Field(ge=0)
    ts_ms: int = Field(ge=0)
    type: EventType
    zone_id: int | None = Field(default=None, ge=0)
    detail: dict = Field(default_factory=dict)

    _validate_patrol_id = field_validator("patrol_id")(_validate_patrol_id)


# --- C2 Analysis (VIS -> AI, filesystem) ---------------------------------


class Detection(BaseModel):
    """One crop detection inside `AnalysisResult.detections` (C2.2).

    `class_` is Python-safe for the JSON key `"class"` via `alias="class"` —
    always construct/serialise with `by_alias=True` (see
    `Store.insert_analysis` and `fake_vis.py::generate_analysis_results`) or
    the alias round-trip breaks.
    """

    model_config = ConfigDict(extra="forbid")

    class_: str = Field(alias="class")
    state: CropState
    count: int = Field(ge=0)
    # No default: required key, nullable value — see c2-analysis.schema.json.
    confidence: float | None = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _confidence_required_unless_undetermined(self) -> Detection:
        """Enforce ICD §C2.2: `confidence` may be null only for `판단불가`.

        A `model_validator(mode="after")` runs once all fields are already
        individually valid, so `self.state` and `self.confidence` are both
        available here. Pydantic calls this automatically after
        `Detection.model_validate(...)` / `Detection(...)`; nothing else in
        the codebase calls it directly.
        """
        if self.state != CropState.UNDETERMINED and self.confidence is None:
            raise ValueError("confidence is required for every state except 판단불가")
        return self


class AnalysisResult(BaseModel):
    """One VIS analysis JSON file, one per image (C2.2).

    Parsed in `ingest/vis_watcher.py::VisWatcher.scan_once` from each
    `*.json` file under `data/analysis/{patrol_id}/`. Constructed directly
    by `devtools/fake_vis.py::generate_analysis_results`, then serialised to
    disk by `fake_vis.py::write_analysis_files`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    image_id: str
    patrol_id: PatrolId
    captured_at_ms: int = Field(ge=0)
    image_path: str
    image_quality: float = Field(ge=0, le=1)
    detections: list[Detection] = Field(default_factory=list)

    _validate_patrol_id = field_validator("patrol_id")(_validate_patrol_id)


# --- C3 Metadata (AI -> WEB, filesystem) ---------------------------------


class StatSummary(BaseModel):
    """avg/min/max/n over one env field's non-null samples in one zone (spec §6).

    Constructed by `pipeline/aggregate.py`'s internal `_stat` helper. Absent
    (not present as a key) rather than null when a zone has zero non-null
    samples for that field — e.g. no temp/humidity sensor fitted — matching
    `c3-metadata.schema.json`'s `env` object having no `required` list.
    """

    model_config = ConfigDict(extra="forbid")

    avg: float
    min: float
    max: float
    n: int = Field(ge=0)


class ZoneEnv(BaseModel):
    """`temp_c`/`humid_pct` stats for one zone. Both keys optional — omitted
    (not null) when that field had zero non-null samples in the zone.
    """

    model_config = ConfigDict(extra="forbid")

    temp_c: StatSummary | None = None
    humid_pct: StatSummary | None = None


class ZoneMetadata(BaseModel):
    """One zone's entry in `PatrolAggregate.zones` / `metadata.json`'s `zones[]`.

    Constructed by `pipeline/aggregate.py`'s internal `_aggregate_zone`
    helper, one per non-transit `ZoneWindow` from `pipeline/segment.py`.
    `image_ids` is always `[]` here — A4's `pipeline/select_images.py`
    populates it; nothing in A2/A3 chooses images.
    """

    model_config = ConfigDict(extra="forbid")

    zone_id: int = Field(ge=1)
    zone_name: str
    status: ReportStatus
    env: ZoneEnv
    observations: dict[str, dict[str, int]] = Field(default_factory=dict)
    undetermined_rate: float | None = Field(default=None, ge=0, le=1)
    flags: list[Literal["재촬영_필요"]] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "low"]


class LlmMetadata(BaseModel):
    """`metadata.json`'s `llm` block. Only `enabled` is required by the schema.

    A2/A3 never call the LLM, so `aggregate()` always produces
    `LlmMetadata(enabled=False)` with every other field left unset. A5 is
    what populates `model`/`prompt_version`/token counts/`cost_usd`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class DataCompleteness(BaseModel):
    """Patrol-wide coverage figures (ICD §C1.3, spec §6's patrol-level outputs).

    `rate` below `COVERAGE_WARN_THRESHOLD` (config.py) warrants a dashboard
    warning per the ICD; `pipeline/aggregate.py` computes it but does not
    itself decide what to do about a low rate — that's the renderer's job (A3).
    """

    model_config = ConfigDict(extra="forbid")

    udp_received: int = Field(ge=0)
    udp_expected: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)
    images_analysed: int = Field(ge=0)
    zone_boundary_confidence: Literal["high", "low"]


class PatrolAggregate(BaseModel):
    """The complete deterministic aggregate for one patrol — mirrors
    `contracts/schemas/c3-metadata.schema.json` field-for-field and *is*
    what gets written to `reports/{patrol_id}/metadata.json` (A3's
    `storage/layout.py`). Also what `render/markdown.py`'s Jinja template
    substitutes every number from (CLAUDE.md hard rule 1: numbers never
    come from the LLM).

    Returned by `pipeline/aggregate.py::aggregate()`. Constructing one
    twice from identical `PatrolSegmentation` input must be byte-identical
    once serialised (CLAUDE.md hard rule 2) — `aggregate()` has no
    randomness or wall-clock dependence, so this holds by construction.
    """

    model_config = ConfigDict(extra="forbid")

    patrol_id: PatrolId
    patrol_date: str
    # Optional here even though the schema requires it on the wire: baking a
    # wall-clock "now" into aggregate()'s output would break hard rule 2's
    # determinism (same input -> byte-identical output). `aggregate()`
    # always leaves this None; `storage/layout.py` stamps the real value
    # into the JSON dict at write time, immediately before it becomes
    # metadata.json — see that module for where "generated_at" actually gets set.
    generated_at: str | None = None
    duration_min: int = Field(ge=0)
    overall_status: ReportStatus
    llm: LlmMetadata
    data_completeness: DataCompleteness
    zones: list[ZoneMetadata] = Field(default_factory=list)

    _validate_patrol_id = field_validator("patrol_id")(_validate_patrol_id)
