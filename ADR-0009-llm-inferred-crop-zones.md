# ADR-0009 — Zones inferred from classified crop type, not DR events

## Status

Accepted (2026-08-23).

## Context

`02-ai-subsystem-spec.md` §5 and `GUIDELINES.md` hard rule 4 specify that zone
boundaries come from DR's `ZONE_ENTER` events (ADR-0003), falling back to
distance-integrated telemetry only when no `ZONE_ENTER` events exist at all
(`pipeline/segment.py::_boundaries_from_distance`).

In the actual deployed system, neither exists: `drive/drive_ver2/` (the
running rover-control code) never sends any C1 telemetry or events to
`ai_report` at all — not `ZONE_ENTER`, not `PATROL_START`/`PATROL_END`, not
raw telemetry. Confirmed by grep: zero references to `ai_report`, UDP
port 9100, `/api/events`, or any `EventType` value anywhere in `drive/`.
The current line-following rover also has no concept of discrete physical
"zones" to begin with — it drives one continuous route.

Separately, `vision/image_analysis/system/classify.py` (filling the gap
`vision/image_analysis/design/README.md` §2-3 describes: VIS never built
real crop classification and expects an LLM to do it) now produces real
per-image crop classifications (`class`/`state`/`count`/`confidence`) for
every captured image, tagged with a `patrol_id`.

Given a patrol already has classified images with a known crop `class` per
detection, and given no reliable physical zone signal exists or is planned
for `drive_ver2`, grouping images by their classified crop type is more
useful and more truthful than reporting zero zones (which is what the
existing distance-fallback silently produces once telemetry is entirely
absent, per `pipeline/segment.py::_boundaries_from_distance`'s
`if not telemetry_sorted: return []` early exit).

## Decision

Add a third segmentation strategy,
`pipeline/segment.py::segment_by_crop_type`, alongside (not replacing) the
existing event-based and distance-based paths:

- One pseudo-zone per distinct crop `class` found across a patrol's
  classified images (`AnalysisResult.detections[].class`), assigned by each
  image's dominant class (highest total `count`).
- Images with zero detections are excluded from zone reporting but still
  counted in coverage, via the existing `zone_id=0` transit-window
  convention `pipeline/segment.py::_build_windows` already uses for the
  event-based path — no new convention needed.
- `boundary_confidence` is always `"low"` — there is no physical boundary
  here at all, and `"low"` already carries that meaning downstream
  (`ZoneMetadata.confidence`, `DataCompleteness.zone_boundary_confidence`).
- Patrol start/end timing still comes from real events where
  possible — `PATROL_START`/`PATROL_END`, now emitted by `web_dashboard`
  around the START/STOP buttons (see `web_dashboard/services/patrol_event_service.py`)
  — falling back to the classified images' own `captured_at_ms` range when
  even those are absent, exactly mirroring `segment_patrol`'s existing
  `_first_ts`/`_last_ts`-then-row-timestamp fallback structure.
- `orchestration.py::run_patrol_pipeline` now calls
  `segment_by_crop_type` instead of `segment_patrol`, since `drive_ver2`
  will never supply `ZONE_ENTER` events for this to have to choose between.
  `segment_patrol` itself is unchanged and stays in use by `cli.py::regenerate`-adjacent
  tests and any future patrol source that *does* emit real zone events.
- `pipeline/aggregate.py::_aggregate_zone` is reused unmodified (via the
  same `ZoneWindow` shape) for the actual disease-ratio/status/undetermined-rate
  math — GUIDELINES.md hard rule 1 ("the LLM never computes numbers") still
  holds: the LLM's only role is classifying individual images
  (`class`/`state`/`count`/`confidence`, already true since ADR-... none,
  this was already the case for VIS's contract); everything from there
  is deterministic Python, unchanged.

## Consequences

- Amends hard rule 4: zone assignment now also legitimately comes from
  classified image content, not solely from `ZONE_ENTER` events or
  distance. The rule's underlying intent (never fabricate zones from
  elapsed time) still holds — crop-type grouping doesn't use elapsed time
  at all.
- `env` (temp/humidity) is empty for crop-type zones today, since
  `drive_ver2` sends no telemetry to correlate against — `ZoneEnv`'s
  existing "absent when zero samples" handling (`pipeline/aggregate.py::_stat`)
  already covers this with no code change.
- `zone_name` for a crop-type zone is derived from the crop class
  (`config.py::CROP_DISPLAY_NAMES`), not `settings.ZONE_NAMES` (which is
  keyed by a physical `zone_id` that no longer means a location here).
- 2026-08-24 follow-up, now done: `web_dashboard/services/patrol_event_service.py::PatrolEventService.end_patrol`
  auto-triggers `classify.py --patrol-id <that patrol>` against
  `received/{date}/`, filtered to that patrol's own window via the new
  `--after-ts-ms`/`--before-ts-ms` flags (file mtime, the same clock the
  dashboard's own `PATROL_START`/`PATROL_END` events are stamped with) —
  see `classify_patrol`'s docstring in `vision/image_analysis/system/classify.py`.
  Still open: a patrol that spans midnight will miss images that landed in
  the previous day's `received/` folder — not handled, judged not worth
  the complexity for a case this rare.
