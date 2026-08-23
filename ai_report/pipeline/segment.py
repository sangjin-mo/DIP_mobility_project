"""① Zone segmentation — spec §5. The single most correctness-critical
algorithm in the subsystem (GUIDELINES.md hard rule 4): zone boundaries come
from `ZONE_ENTER` events, never from elapsed time or distance-derived
guesswork, unless no events exist at all.

Primary path (events present): zone `k` spans
`[ZONE_ENTER[k].ts_ms, ZONE_ENTER[k+1].ts_ms)`, using each event's own
`zone_id` as the zone label. The final zone extends to `PATROL_END.ts_ms`.
Records before the first `ZONE_ENTER` form a transit segment, `zone_id=0`,
excluded from zone reporting but still returned (so coverage/dedup
accounting upstream isn't affected). An `EMERGENCY_STOP` or any other event
happening mid-zone never moves a boundary, because boundaries are only ever
read from `ZONE_ENTER` timestamps — this is what the acceptance test
`test_emergency_stop_does_not_shift_boundary` in `tests/test_segment.py`
exists to prove.

Fallback path (no `ZONE_ENTER` events): boundaries are estimated from
cumulative distance (`speed_mps` integrated over telemetry, excluding
`STOPPED`/`EMERGENCY` intervals) divided into `settings.ROUTE_ZONE_COUNT`
equal-distance zones spanning `settings.ROUTE_TOTAL_DISTANCE_M`.

> [!FLAG] `ROUTE_ZONE_COUNT` / `ROUTE_TOTAL_DISTANCE_M` are not defined
> anywhere in `02-ai-subsystem-spec.md` §5's fallback description — the
> spec says "divide by configured route zone distances" but never says
> where that configuration comes from. This module's assumption (equal
> division of one configured total distance into one configured zone
> count) is isolated in `config.py` and documented there; see the matching
> `[!FLAG]` in `02-ai-subsystem-spec.md` §5.

Called by: whatever loads a patrol's rows from `Store` and hands them to
`segment_patrol` — currently only `tests/test_segment.py` and
`tests/test_aggregate.py` (via its own loading), since the orchestration
that will call this automatically on `PATROL_END` + VIS `_COMPLETE` doesn't
exist yet (that's a later phase's `cli.py` addition). Calls nothing outside
this module and `ai_report.models`/`ai_report.config`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ai_report.config import Settings
from ai_report.models import AnalysisResult, DriveState, EventMessage, EventType, TelemetryPacket


@dataclass
class ZoneWindow:
    """All rows assigned to one zone's time interval `[start_ts_ms, end_ts_ms]`.

    `zone_id=0` is the transit segment (before the first `ZONE_ENTER`, or
    entirely absent when segmentation starts right at zone 1). The end
    bound is inclusive only for the last window in a `PatrolSegmentation`
    (so a sample landing exactly on `PATROL_END.ts_ms` isn't dropped);
    every other window's end is exclusive, matching spec §5's
    half-open-interval definition.
    """

    zone_id: int
    start_ts_ms: int
    end_ts_ms: int
    telemetry: list[TelemetryPacket] = field(default_factory=list)
    analysis: list[AnalysisResult] = field(default_factory=list)
    events: list[EventMessage] = field(default_factory=list)


@dataclass
class PatrolSegmentation:
    """Output of `segment_patrol`: every row grouped into `ZoneWindow`s, plus
    the confidence flag that tells `pipeline/aggregate.py` (and eventually
    the renderer) whether boundaries came from real events or were estimated.
    """

    patrol_id: str
    boundary_confidence: Literal["high", "low"]
    windows: list[ZoneWindow]
    patrol_start_ts_ms: int
    patrol_end_ts_ms: int

    def zones(self) -> list[ZoneWindow]:
        """Windows excluding the zone_id=0 transit segment, sorted by zone_id.

        This is what `pipeline/aggregate.py::aggregate` iterates over —
        transit rows are counted in patrol-wide coverage but never produce
        a `ZoneMetadata` entry (spec §5: "excluded from zone reporting but
        counted in coverage").
        """
        return sorted((w for w in self.windows if w.zone_id != 0), key=lambda w: w.zone_id)

    def obstruction_counts(self) -> dict[int, dict[str, int]]:
        """Count `EMERGENCY_STOP`/`LINE_LOST` events per zone.

        Shared by two A4+ consumers that both need this and neither of
        which get it from `PatrolAggregate` — `ZoneMetadata` deliberately
        excludes raw event detail since it's not part of
        `c3-metadata.schema.json` (see `pipeline/aggregate.py`'s
        docstring): `render/markdown.py`'s 통로 장애 요인 section, and
        `pipeline/payload.py::build_payload`'s `Payload.obstructions` (the
        LLM needs this to write about drive events at all — spec §9.2's
        prompt explicitly asks for it). A zone with no obstruction events
        is omitted from the result entirely, not present with an empty dict.
        """
        result: dict[int, dict[str, int]] = {}
        for window in self.zones():
            counts: dict[str, int] = {}
            for evt in window.events:
                if evt.type in (EventType.EMERGENCY_STOP, EventType.LINE_LOST):
                    counts[evt.type.value] = counts.get(evt.type.value, 0) + 1
            if counts:
                result[window.zone_id] = counts
        return result


def segment_patrol(
    patrol_id: str,
    telemetry: list[TelemetryPacket],
    events: list[EventMessage],
    analysis: list[AnalysisResult],
    settings: Settings,
) -> PatrolSegmentation:
    """Assign every telemetry/analysis/event row to a zone window.

    Determines `patrol_start_ts_ms`/`patrol_end_ts_ms` from the
    `PATROL_START`/`PATROL_END` events when present, falling back to the
    earliest/latest row timestamp otherwise (a patrol missing its own
    boundary events shouldn't crash segmentation — it should just lose
    precision at the very edges). Then picks the primary event-based path
    when any `ZONE_ENTER` events exist, else the distance-estimation
    fallback — see the module docstring for both. Finally assigns every
    row to the window whose interval contains its timestamp via
    `_build_windows`.

    Pure and deterministic: same four arguments always produce the same
    `PatrolSegmentation` (GUIDELINES.md hard rule 2). No I/O — callers are
    responsible for loading `telemetry`/`events`/`analysis` from `Store` first.
    """
    events_sorted = sorted(events, key=lambda e: e.ts_ms)
    telemetry_sorted = sorted(telemetry, key=lambda t: t.ts_ms)
    analysis_sorted = sorted(analysis, key=lambda a: a.captured_at_ms)

    patrol_start_ts_ms = _first_ts(events_sorted, EventType.PATROL_START)
    patrol_end_ts_ms = _last_ts(events_sorted, EventType.PATROL_END)
    if patrol_start_ts_ms is None:
        patrol_start_ts_ms = telemetry_sorted[0].ts_ms if telemetry_sorted else 0
    if patrol_end_ts_ms is None:
        candidates = [t.ts_ms for t in telemetry_sorted] + [a.captured_at_ms for a in analysis_sorted]
        patrol_end_ts_ms = max(candidates) if candidates else patrol_start_ts_ms

    zone_enters = [e for e in events_sorted if e.type == EventType.ZONE_ENTER]

    if zone_enters:
        boundaries = _boundaries_from_events(zone_enters, patrol_end_ts_ms)
        confidence: Literal["high", "low"] = "high"
    else:
        boundaries = _boundaries_from_distance(telemetry_sorted, patrol_end_ts_ms, settings)
        confidence = "low"

    windows = _build_windows(
        boundaries, patrol_start_ts_ms, telemetry_sorted, events_sorted, analysis_sorted
    )

    return PatrolSegmentation(
        patrol_id=patrol_id,
        boundary_confidence=confidence,
        windows=windows,
        patrol_start_ts_ms=patrol_start_ts_ms,
        patrol_end_ts_ms=patrol_end_ts_ms,
    )


def _first_ts(events_sorted: list[EventMessage], event_type: EventType) -> int | None:
    """`ts_ms` of the first event of `event_type` in a `ts_ms`-sorted list, or None."""
    return next((e.ts_ms for e in events_sorted if e.type == event_type), None)


def _last_ts(events_sorted: list[EventMessage], event_type: EventType) -> int | None:
    """`ts_ms` of the last event of `event_type` in a `ts_ms`-sorted list, or None.

    Walks in reverse rather than filtering-then-indexing `[-1]`, to avoid
    building an intermediate list for what's normally a short scan.
    """
    for e in reversed(events_sorted):
        if e.type == event_type:
            return e.ts_ms
    return None


def _boundaries_from_events(
    zone_enters: list[EventMessage], patrol_end_ts_ms: int
) -> list[tuple[int, int, int]]:
    """Primary path: one boundary per `ZONE_ENTER`, in event order.

    Returns `(zone_id, start_ts_ms, end_ts_ms)` tuples. `zone_id` is the
    event's own `zone_id` field (ICD §C1.2: present on `ZONE_ENTER`) — the
    positional index is only a fallback for a malformed event missing it,
    which shouldn't happen given the schema but is handled rather than
    crashing. Each zone's `end_ts_ms` is the *next* `ZONE_ENTER`'s
    `ts_ms`, or `patrol_end_ts_ms` for the last one — this is the entire
    mechanism that makes an `EMERGENCY_STOP` mid-zone unable to shift a
    boundary: nothing here reads event types other than `ZONE_ENTER`.
    """
    boundaries: list[tuple[int, int, int]] = []
    for i, evt in enumerate(zone_enters):
        zone_id = evt.zone_id if evt.zone_id is not None else i + 1
        start_ts_ms = evt.ts_ms
        end_ts_ms = zone_enters[i + 1].ts_ms if i + 1 < len(zone_enters) else patrol_end_ts_ms
        boundaries.append((zone_id, start_ts_ms, end_ts_ms))
    return boundaries


def _boundaries_from_distance(
    telemetry_sorted: list[TelemetryPacket], patrol_end_ts_ms: int, settings: Settings
) -> list[tuple[int, int, int]]:
    """Fallback path: estimate boundaries from cumulative distance travelled.

    Builds a step function of `(ts_ms, cumulative_distance_m)` by summing
    `speed_mps * dt` between consecutive telemetry samples, treating any
    interval ending in a `STOPPED` or `EMERGENCY` sample as zero distance
    (spec §5: "excluding intervals where state == STOPPED or EMERGENCY").
    Divides the configured total route distance into
    `settings.ROUTE_ZONE_COUNT` equal-length zones and finds, for each zone
    boundary distance, the first timestamp at which cumulative distance
    reaches it. Returns the same `(zone_id, start_ts_ms, end_ts_ms)` shape
    as `_boundaries_from_events` so `_build_windows` doesn't need to know
    which path produced its input. No transit segment is modelled here —
    zone 1 starts at the first telemetry sample, since there is no event
    marking a transit period in this path (an assumption; see this module's
    `[!FLAG]`).
    """
    if not telemetry_sorted:
        return []

    zone_count = settings.ROUTE_ZONE_COUNT
    zone_distance_m = settings.ROUTE_TOTAL_DISTANCE_M / zone_count

    samples: list[tuple[int, float]] = [(telemetry_sorted[0].ts_ms, 0.0)]
    cumulative_m = 0.0
    prev = telemetry_sorted[0]
    for curr in telemetry_sorted[1:]:
        dt_s = max(0.0, (curr.ts_ms - prev.ts_ms) / 1000.0)
        moving = curr.drive.state not in (DriveState.STOPPED, DriveState.EMERGENCY)
        if moving:
            cumulative_m += prev.drive.speed_mps * dt_s
        samples.append((curr.ts_ms, cumulative_m))
        prev = curr

    def ts_at_distance(target_m: float) -> int:
        for ts_ms, dist_m in samples:
            if dist_m >= target_m:
                return ts_ms
        return samples[-1][0]

    boundaries: list[tuple[int, int, int]] = []
    for zone_id in range(1, zone_count + 1):
        start_ts_ms = samples[0][0] if zone_id == 1 else ts_at_distance(zone_distance_m * (zone_id - 1))
        end_ts_ms = patrol_end_ts_ms if zone_id == zone_count else ts_at_distance(zone_distance_m * zone_id)
        boundaries.append((zone_id, start_ts_ms, end_ts_ms))
    return boundaries


def _build_windows(
    boundaries: list[tuple[int, int, int]],
    patrol_start_ts_ms: int,
    telemetry_sorted: list[TelemetryPacket],
    events_sorted: list[EventMessage],
    analysis_sorted: list[AnalysisResult],
) -> list[ZoneWindow]:
    """Turn a boundary list into `ZoneWindow`s and file every row into one.

    Prepends a `zone_id=0` transit window when the first boundary starts
    after `patrol_start_ts_ms` (rows before the first `ZONE_ENTER`, or
    before zone 1 in the fallback path if it doesn't start at t=0). Every
    window's end is exclusive except the last one's, which is inclusive —
    see `ZoneWindow`'s docstring for why.
    """
    windows: list[ZoneWindow] = []

    if boundaries and boundaries[0][1] > patrol_start_ts_ms:
        windows.append(
            _fill_window(0, patrol_start_ts_ms, boundaries[0][1], False,
                         telemetry_sorted, events_sorted, analysis_sorted)
        )

    for i, (zone_id, start_ts_ms, end_ts_ms) in enumerate(boundaries):
        is_last = i == len(boundaries) - 1
        windows.append(
            _fill_window(zone_id, start_ts_ms, end_ts_ms, is_last,
                         telemetry_sorted, events_sorted, analysis_sorted)
        )

    return windows


def dominant_crop_class(result: AnalysisResult) -> str | None:
    """The crop class with the most total detections in `result`, or `None`
    if it has no detections at all. Ties break alphabetically (descending
    count, then ascending class name) so the choice is deterministic
    regardless of dict/insertion order.

    Not module-private: `segment_by_crop_type` uses it to build each
    `ZoneWindow`'s group, and `pipeline/aggregate.py::aggregate_zones_by_crop_type`
    uses it again afterward to recover which crop class a given zone_id
    represents (every image in one crop-type `ZoneWindow` shares the same
    dominant class by construction, so re-deriving it from the window's
    first analysis result is exact, not a guess).
    """
    if not result.detections:
        return None
    totals: dict[str, int] = {}
    for d in result.detections:
        totals[d.class_] = totals.get(d.class_, 0) + d.count
    return min(totals, key=lambda c: (-totals[c], c))


def segment_by_crop_type(
    patrol_id: str,
    events: list[EventMessage],
    analysis: list[AnalysisResult],
) -> PatrolSegmentation:
    """Group classified images into pseudo-zones by crop type (ADR-0009).

    Used instead of `segment_patrol` when there is no reliable physical
    zone signal to segment by at all -- `drive_ver2` never sends
    `ZONE_ENTER` events or any telemetry (see ADR-0009's Context), so
    `segment_patrol`'s existing distance fallback always degrades to zero
    zones once telemetry is entirely absent
    (`_boundaries_from_distance`'s `if not telemetry_sorted: return []`).
    Grouping by each image's already-classified dominant crop type
    (`_dominant_class`) is more useful than reporting nothing.

    One `ZoneWindow` per distinct crop class present across `analysis`,
    `zone_id` assigned 1, 2, 3... in alphabetical class order (deterministic
    regardless of image arrival order -- GUIDELINES.md hard rule 2). Images
    with no detections at all go into the same `zone_id=0` transit window
    `_build_windows` already uses for rows before the first `ZONE_ENTER` on
    the event-based path -- excluded from zone reporting but still counted
    toward `images_analysed`, no new convention needed.

    `patrol_start_ts_ms`/`patrol_end_ts_ms` come from `PATROL_START`/
    `PATROL_END` events when present (now real, since `web_dashboard`
    emits them around the START/STOP buttons -- see
    `web_dashboard/services/patrol_event_service.py`), falling back to the
    classified images' own `captured_at_ms` range otherwise, mirroring
    `segment_patrol`'s own event-then-row-timestamp fallback structure
    exactly (reuses the same `_first_ts`/`_last_ts` helpers).

    `boundary_confidence` is always `"low"` -- there is no physical
    boundary here at all, and downstream fields
    (`ZoneMetadata.confidence`, `DataCompleteness.zone_boundary_confidence`)
    already treat `"low"` as exactly that meaning.

    Pure and deterministic like `segment_patrol` -- no I/O, no `Settings`
    dependency (no config threshold this path needs). Called by
    `orchestration.py::run_patrol_pipeline`; directly by
    `tests/test_segment.py`.
    """
    events_sorted = sorted(events, key=lambda e: e.ts_ms)
    analysis_sorted = sorted(analysis, key=lambda a: a.captured_at_ms)

    patrol_start_ts_ms = _first_ts(events_sorted, EventType.PATROL_START)
    patrol_end_ts_ms = _last_ts(events_sorted, EventType.PATROL_END)
    if patrol_start_ts_ms is None:
        patrol_start_ts_ms = analysis_sorted[0].captured_at_ms if analysis_sorted else 0
    if patrol_end_ts_ms is None:
        patrol_end_ts_ms = analysis_sorted[-1].captured_at_ms if analysis_sorted else patrol_start_ts_ms

    groups: dict[str, list[AnalysisResult]] = {}
    untagged: list[AnalysisResult] = []
    for result in analysis_sorted:
        crop_class = dominant_crop_class(result)
        if crop_class is None:
            untagged.append(result)
        else:
            groups.setdefault(crop_class, []).append(result)

    windows: list[ZoneWindow] = []
    if untagged:
        windows.append(
            ZoneWindow(zone_id=0, start_ts_ms=patrol_start_ts_ms, end_ts_ms=patrol_end_ts_ms, analysis=untagged)
        )
    for zone_id, crop_class in enumerate(sorted(groups), start=1):
        windows.append(
            ZoneWindow(
                zone_id=zone_id,
                start_ts_ms=patrol_start_ts_ms,
                end_ts_ms=patrol_end_ts_ms,
                analysis=groups[crop_class],
            )
        )

    return PatrolSegmentation(
        patrol_id=patrol_id,
        boundary_confidence="low",
        windows=windows,
        patrol_start_ts_ms=patrol_start_ts_ms,
        patrol_end_ts_ms=patrol_end_ts_ms,
    )


def _fill_window(
    zone_id: int,
    start_ts_ms: int,
    end_ts_ms: int,
    inclusive_end: bool,
    telemetry_sorted: list[TelemetryPacket],
    events_sorted: list[EventMessage],
    analysis_sorted: list[AnalysisResult],
) -> ZoneWindow:
    """Filter each sorted row list down to the ones inside `[start_ts_ms, end_ts_ms)` (or `]` if `inclusive_end`).

    O(zones × rows) — a linear scan per window rather than a binary search
    on the sorted lists. Simple and fast enough at this data scale (a
    20-minute patrol is ~1200 telemetry rows, single-digit zones); revisit
    only if patrol size grows by orders of magnitude.
    """

    def within(ts_ms: int) -> bool:
        return start_ts_ms <= ts_ms <= end_ts_ms if inclusive_end else start_ts_ms <= ts_ms < end_ts_ms

    return ZoneWindow(
        zone_id=zone_id,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        telemetry=[t for t in telemetry_sorted if within(t.ts_ms)],
        analysis=[a for a in analysis_sorted if within(a.captured_at_ms)],
        events=[e for e in events_sorted if within(e.ts_ms)],
    )
