"""② Aggregation — spec §6. Pure, deterministic statistics over a
`PatrolSegmentation`. GUIDELINES.md hard rule 1: every number in the eventual
report comes from here, never from the LLM. Hard rule 2: `aggregate()` must
be network-free and produce byte-identical output for identical input —
true by construction here, since nothing in this module reads the clock,
generates randomness, or performs I/O.

`aggregate()`'s return value, `PatrolAggregate` (in `models.py`), mirrors
`contracts/schemas/c3-metadata.schema.json` field-for-field and is exactly
what `storage/layout.py` (A3) writes as `metadata.json` — see that model's
docstring for why `generated_at` is deliberately left unset here.

Called by: whatever runs the pipeline for a patrol — currently only
`tests/test_aggregate.py`; production orchestration (on `PATROL_END` +
VIS `_COMPLETE`) is a later phase's addition. Calls `pipeline/segment.py`'s
`PatrolSegmentation`/`ZoneWindow` (as input, not by calling `segment_patrol`
itself — the caller is responsible for segmenting first) and
`ai_report.models`/`ai_report.config`.
"""

from __future__ import annotations

from ai_report.config import Settings
from ai_report.models import (
    CropState,
    DataCompleteness,
    EventType,
    LlmMetadata,
    PatrolAggregate,
    ReportStatus,
    StatSummary,
    ZoneEnv,
    ZoneMetadata,
)
from ai_report.pipeline.segment import PatrolSegmentation, ZoneWindow

# Zone status rule (spec §6). Order matters: 이상 is checked first, then 주의,
# so a zone meeting both the 이상 and 주의 conditions is reported as 이상 —
# the more severe status wins. Both ratios come from config.py, not a magic
# number here; see UNDETERMINED_FLAG_THRESHOLD (재촬영 flag) below and the
# two literal thresholds (0.15, 0.05) from spec §6, which the spec states as
# fixed rule constants rather than tunable config.
_ABNORMAL_DISEASE_RATIO = 0.15
_CAUTION_DISEASE_RATIO = 0.05

_STATUS_SEVERITY = {ReportStatus.NORMAL: 0, ReportStatus.CAUTION: 1, ReportStatus.ABNORMAL: 2}


def aggregate(
    segmentation: PatrolSegmentation,
    udp_received: int,
    udp_expected: int,
    settings: Settings,
) -> PatrolAggregate:
    """Compute the full deterministic aggregate for one already-segmented patrol.

    `udp_received`/`udp_expected` are passed in rather than recomputed here
    (they come from `Store.received_telemetry_seqs`/`max_telemetry_seq`,
    which do touch the database) — keeping this function itself free of any
    I/O, per hard rule 2. Iterates `segmentation.zones()` (which already
    excludes the zone_id=0 transit window) to build one `ZoneMetadata` per
    zone via `_aggregate_zone`, then takes `overall_status` as the worst
    zone status (spec §6) — `정상` if there are no zones at all.

    `duration_min` is `(patrol_end_ts_ms - patrol_start_ts_ms) / 60000`,
    rounded to the nearest minute. `patrol_date` is read directly out of
    `patrol_id`'s `YYYYMMDD` prefix rather than derived from a timestamp,
    to sidestep timezone ambiguity entirely — the patrol_id already carries
    the answer.
    """
    zones = [_aggregate_zone(w, segmentation.boundary_confidence, settings) for w in segmentation.zones()]
    overall_status = _worst_status([z.status for z in zones]) if zones else ReportStatus.NORMAL

    duration_min = round((segmentation.patrol_end_ts_ms - segmentation.patrol_start_ts_ms) / 60_000)
    rate = (udp_received / udp_expected) if udp_expected else 0.0
    images_analysed = sum(len(w.analysis) for w in segmentation.windows)

    return PatrolAggregate(
        patrol_id=segmentation.patrol_id,
        patrol_date=_patrol_date(segmentation.patrol_id),
        duration_min=duration_min,
        overall_status=overall_status,
        llm=LlmMetadata(enabled=False),
        data_completeness=DataCompleteness(
            udp_received=udp_received,
            udp_expected=udp_expected,
            rate=rate,
            images_analysed=images_analysed,
            zone_boundary_confidence=segmentation.boundary_confidence,
        ),
        zones=zones,
    )


def _patrol_date(patrol_id: str) -> str:
    """`YYYYMMDD_HHMM` -> `YYYY-MM-DD`, read directly from the patrol_id string."""
    return f"{patrol_id[0:4]}-{patrol_id[4:6]}-{patrol_id[6:8]}"


def _worst_status(statuses: list[ReportStatus]) -> ReportStatus:
    """The single most severe status in `statuses`, by `_STATUS_SEVERITY`."""
    return max(statuses, key=lambda s: _STATUS_SEVERITY[s])


def _stat(values: list[float]) -> StatSummary | None:
    """avg/min/max/n over `values`, or None if `values` is empty.

    `avg` is rounded to 2 decimal places to keep `metadata.json` readable;
    `min`/`max` are left exact. Called by `_aggregate_zone` for both
    `temp_c` and `humid_pct`.
    """
    if not values:
        return None
    return StatSummary(avg=round(sum(values) / len(values), 2), min=min(values), max=max(values), n=len(values))


def _aggregate_zone(
    window: ZoneWindow, boundary_confidence: str, settings: Settings
) -> ZoneMetadata:
    """Compute one zone's `ZoneMetadata` from its `ZoneWindow`.

    Three things happen here that are worth calling out:

    1. **Env stats** (`_stat`) run only over non-null samples — a sensor
       read failure (or no sensor fitted at all) removes that sample from
       the average rather than counting as zero, per ICD §C1.1.
    2. **Two different denominators** for the two ratios spec §6 defines:
       `disease_ratio` divides by 정상+미성숙+병충해_의심 (excludes 판단불가
       entirely), while `undetermined_rate` divides by all four states
       summed. Mixing these up is the easiest way to get this function
       subtly wrong, so they're computed as clearly separate expressions
       below rather than shared.
    3. **Status order**: 이상 is checked before 주의, so the more severe
       status wins when a zone qualifies for both (see the module-level
       comment on `_ABNORMAL_DISEASE_RATIO`/`_CAUTION_DISEASE_RATIO`).

    `dwell_s`/`image_count`/raw `drive_events` from spec §6's per-zone
    output table are deliberately not returned as part of `ZoneMetadata` —
    they're not in `c3-metadata.schema.json`, so they exist here only as
    local values used to derive `status`/`flags` and then discarded.
    """
    temp_values = [t.env.temp_c for t in window.telemetry if t.env.temp_c is not None]
    humid_values = [t.env.humid_pct for t in window.telemetry if t.env.humid_pct is not None]
    env = ZoneEnv(temp_c=_stat(temp_values), humid_pct=_stat(humid_values))

    state_totals: dict[CropState, int] = dict.fromkeys(CropState, 0)
    observations: dict[str, dict[str, int]] = {}
    for result in window.analysis:
        for det in result.detections:
            state_totals[det.state] += det.count
            class_counts = observations.setdefault(det.class_, {})
            class_counts[det.state.value] = class_counts.get(det.state.value, 0) + det.count

    determined_total = (
        state_totals[CropState.NORMAL] + state_totals[CropState.IMMATURE] + state_totals[CropState.SUSPECTED_DISEASE]
    )
    all_total = determined_total + state_totals[CropState.UNDETERMINED]

    disease_ratio = (state_totals[CropState.SUSPECTED_DISEASE] / determined_total) if determined_total > 0 else 0.0
    undetermined_rate = (state_totals[CropState.UNDETERMINED] / all_total) if all_total > 0 else None

    flags: list[str] = []
    if undetermined_rate is not None and undetermined_rate > settings.UNDETERMINED_FLAG_THRESHOLD:
        flags.append("재촬영_필요")

    emergency_stop_occurred = any(e.type == EventType.EMERGENCY_STOP for e in window.events)

    if disease_ratio > _ABNORMAL_DISEASE_RATIO:
        status = ReportStatus.ABNORMAL
    elif disease_ratio > _CAUTION_DISEASE_RATIO or flags or emergency_stop_occurred:
        status = ReportStatus.CAUTION
    else:
        status = ReportStatus.NORMAL

    return ZoneMetadata(
        zone_id=window.zone_id,
        zone_name=settings.ZONE_NAMES.get(window.zone_id, f"{window.zone_id}구역"),
        status=status,
        env=env,
        observations=observations,
        undetermined_rate=undetermined_rate,
        flags=flags,
        image_ids=[],  # populated by pipeline/select_images.py, A4
        confidence=boundary_confidence,
    )
