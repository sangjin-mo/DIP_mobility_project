"""⑥ Markdown rendering — spec §10, ICD §C3.2. Jinja2, never model output
(CLAUDE.md hard rule 3): the LLM's structured JSON (when A5 exists and is
enabled) only supplies prose strings the template drops into fixed slots.
Every number comes from `PatrolAggregate` (hard rule 1), substituted by the
template exactly as `aggregate()` computed it.

`render/templates/report.md.j2` is the only place the six-H2-section
structure (ICD §C3.2) is expressed — because it's a template with no
conditional *removal* of a whole section, "all six H2 sections always
exist, always in this order" (hard rule 3 made concrete) holds for every
call, LLM enabled or not, zero zones or many. See
`tests/test_markdown.py::test_six_sections_present_and_ordered`.

Per-zone line formatting (observation table rows, env summary, obstruction
counts, recommendations) is built as plain strings in `_build_zone_views`
*before* the template runs, rather than inline in Jinja. This isn't a style
preference: an earlier version built these strings with inline
`{% if %}...{% endif %}` at the end of a content line, and Jinja's
`trim_blocks` — which strips the newline immediately after any `%}` — ate
the newline after those trailing `{% endif %}` tags, silently concatenating
every zone's line onto one unreadable run-on line. Pre-formatting in Python
means every template line either is a bare block tag (safely trimmed) or
ends in a `{{ expression }}` (never trimmed), so there is no line where
content and a block-tag boundary coincide.

Called by: whatever assembles a report for storage — currently only
`tests/test_markdown.py`; `storage/layout.py::write_report` (A3) is the
production caller once pipeline orchestration exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ai_report.models import EventType, PatrolAggregate, ZoneMetadata
from ai_report.pipeline.segment import PatrolSegmentation

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_OBSTRUCTION_EVENT_TYPES = (EventType.EMERGENCY_STOP, EventType.LINE_LOST)


class LlmReportContent(Protocol):
    """Structural shape the template expects from A5's LLM output, so this
    module can be tested and type-checked without `llm/schema.py` existing
    yet. A5's real structured-output model should end up satisfying this
    shape (or the template's `llm.*` references need updating together).
    """

    summary_ko: str
    zone_notes: dict[int, str]


@dataclass
class ZoneView:
    """One zone's content, fully pre-formatted to strings the template just interpolates.

    Built by `_build_zone_views` from a `ZoneMetadata` plus that zone's
    obstruction-event counts. `observation_lines`/`obstruction_line`/
    `recommendation_line` are `None`/empty exactly when that zone has
    nothing to say for that section, so the template can test for absence
    with a plain `{% if %}` rather than re-deriving the condition.
    """

    zone_id: int
    zone_name: str
    status_label: str
    flags_label: str
    observation_lines: list[str]
    env_line: str
    obstruction_line: str | None
    recommendation_line: str | None


def _obstructions_by_zone(segmentation: PatrolSegmentation) -> dict[int, dict[str, int]]:
    """Count `EMERGENCY_STOP`/`LINE_LOST` events per zone, for the 통로 장애 요인 section.

    Not derived from `PatrolAggregate` because `ZoneMetadata` deliberately
    excludes raw event detail (it's not part of `c3-metadata.schema.json` —
    see `pipeline/aggregate.py`'s docstring). Reads directly from
    `segmentation.zones()` instead, which still has each zone's full event
    list. A zone with no obstruction events is omitted from the result
    entirely.
    """
    result: dict[int, dict[str, int]] = {}
    for window in segmentation.zones():
        counts: dict[str, int] = {}
        for evt in window.events:
            if evt.type in _OBSTRUCTION_EVENT_TYPES:
                counts[evt.type.value] = counts.get(evt.type.value, 0) + 1
        if counts:
            result[window.zone_id] = counts
    return result


def _env_line(zone: ZoneMetadata) -> str:
    """`"{zone_id}구역: 평균 X°C (표본 N개) / 평균 Y% (표본 N개)"`, with a stated
    reason instead of a number when that field has no non-null samples.
    """
    temp = f"평균 {zone.env.temp_c.avg}°C (표본 {zone.env.temp_c.n}개)" if zone.env.temp_c else "온도 데이터 없음"
    humid = (
        f"평균 {zone.env.humid_pct.avg}% (표본 {zone.env.humid_pct.n}개)" if zone.env.humid_pct else "습도 데이터 없음"
    )
    return f"{zone.zone_id}구역: {temp} / {humid}"


def _observation_lines(zone: ZoneMetadata) -> list[str]:
    """One Markdown table row per (crop, state) with a non-zero count, in insertion order."""
    return [
        f"| {crop} | {state} | {n} |"
        for crop, states in zone.observations.items()
        for state, n in states.items()
    ]


def _obstruction_line(zone_id: int, counts: dict[str, int]) -> str:
    """`"{zone_id}구역: EMERGENCY_STOP 2회, LINE_LOST 1회"`."""
    parts = ", ".join(f"{event_type} {n}회" for event_type, n in counts.items())
    return f"{zone_id}구역: {parts}"


def _recommendation_line(zone: ZoneMetadata) -> str | None:
    """A deterministic stand-in for A5's LLM-authored recommendations: for now,
    the only rule-derivable recommendation is "recapture a flagged zone" —
    everything else in spec §9's `recommended_actions_ko` genuinely needs
    the model's judgement and has no fallback here. `None` when the zone
    has no flags at all.
    """
    if "재촬영_필요" not in zone.flags:
        return None
    rate_pct = round(zone.undetermined_rate * 100) if zone.undetermined_rate is not None else 0
    return f"{zone.zone_id}구역: 재촬영 권장 (판단불가 비율 {rate_pct}%)"


def _build_zone_views(agg: PatrolAggregate, obstructions: dict[int, dict[str, int]]) -> list[ZoneView]:
    """Turn `agg.zones` into fully pre-formatted `ZoneView`s. See module docstring for why."""
    views = []
    for zone in agg.zones:
        views.append(
            ZoneView(
                zone_id=zone.zone_id,
                zone_name=zone.zone_name,
                status_label=zone.status.value,
                flags_label=f" ({', '.join(zone.flags)})" if zone.flags else "",
                observation_lines=_observation_lines(zone),
                env_line=_env_line(zone),
                obstruction_line=_obstruction_line(zone.zone_id, obstructions[zone.zone_id])
                if zone.zone_id in obstructions
                else None,
                recommendation_line=_recommendation_line(zone),
            )
        )
    return views


def _jinja_env() -> Environment:
    """Build the Jinja environment `render_report` renders with.

    `StrictUndefined` turns a typo'd template variable into an immediate
    `UndefinedError` at render time instead of silently rendering an empty
    string — worth it here since a silently-blank field in a report a farm
    manager reads is exactly the kind of failure that's hard to notice.
    `trim_blocks`/`lstrip_blocks` keep the rendered Markdown free of the
    blank lines bare `{% %}` control-tag lines would otherwise leave behind
    — safe now that every content line ends in `{{ }}`, never `%}` (see
    module docstring).
    """
    return Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )


def render_report(
    agg: PatrolAggregate,
    segmentation: PatrolSegmentation,
    llm: LlmReportContent | None = None,
    coverage_warn_threshold: float = 0.90,
) -> str:
    """Render `report.md.j2` to a Markdown string. Pure — no I/O, no network.

    `agg` supplies every number and status, pre-formatted per zone by
    `_build_zone_views` before the template ever runs. `segmentation`
    supplies the per-zone obstruction-event detail `agg` doesn't carry (see
    `_obstructions_by_zone`). `llm` is `None` for every report until A5
    exists (or on any A5 fallback) — the template's `{% if llm %}` branches
    handle that case explicitly. `coverage_warn_threshold` defaults to
    `config.COVERAGE_WARN_THRESHOLD`'s value but is passed as a plain float
    rather than a `Settings` object, keeping this function's dependency
    surface to exactly what it renders.
    """
    obstructions = _obstructions_by_zone(segmentation)
    zone_views = _build_zone_views(agg, obstructions)

    template = _jinja_env().get_template("report.md.j2")
    return template.render(
        agg=agg,
        llm=llm,
        zone_views=zone_views,
        obstruction_lines=[zv.obstruction_line for zv in zone_views if zv.obstruction_line],
        recommendation_lines=[zv.recommendation_line for zv in zone_views if zv.recommendation_line],
        coverage_warn_threshold=coverage_warn_threshold,
    )
