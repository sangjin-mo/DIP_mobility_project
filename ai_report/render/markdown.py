"""⑥ Markdown rendering — spec §10, ICD §C3.2. Jinja2, never model output
(CLAUDE.md hard rule 3): the LLM's structured JSON only supplies prose
strings the template drops into fixed slots next to figures that already
came from `PatrolAggregate`. Every number comes from `PatrolAggregate`
(hard rule 1) — `LlmReportOutput` (A5's `llm/schema.py`) has no numeric
field anywhere, so there is nowhere for a model-computed number to enter
the rendered report even by accident.

`render/templates/report.md.j2` is the only place the six-H2-section
structure (ICD §C3.2) is expressed — because it's a template with no
conditional *removal* of a whole section, "all six H2 sections always
exist, always in this order" (hard rule 3 made concrete) holds for every
call, LLM enabled or not, zero zones or many. See
`tests/test_markdown.py::test_six_sections_present_and_ordered`.

Per-zone line formatting (observation table rows, env summary, obstruction
counts, recommendations, and now the LLM's per-zone prose) is built as
plain strings in `_build_zone_views` *before* the template runs, rather
than inline in Jinja. This isn't a style preference: an earlier version
built these strings with inline `{% if %}...{% endif %}` at the end of a
content line, and Jinja's `trim_blocks` — which strips the newline
immediately after any `%}` — ate the newline after those trailing
`{% endif %}` tags, silently concatenating every zone's line onto one
unreadable run-on line. Pre-formatting in Python means every template line
either is a bare block tag (safely trimmed) or ends in a
`{{ expression }}` (never trimmed), so there is no line where content and
a block-tag boundary coincide.

A zone_id in `llm.zones` that doesn't match any `agg.zones` entry is
silently absent from the rendered report — not an error here. Dropping
and logging an unknown zone_id is `llm/client.py::_drop_unknown_zones`'s
job (spec §9); this module only ever looks up LLM zone notes *by* a real
`agg` zone_id, so an orphaned LLM zone note simply has nothing to attach
to and never appears, whether or not the upstream filtering already ran.

Called by: whatever assembles a report for storage — currently only
`tests/test_markdown.py`; `storage/layout.py::write_report` is the
production caller once pipeline orchestration exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ai_report.llm.schema import LlmReportOutput
from ai_report.models import PatrolAggregate, ZoneMetadata

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class ZoneView:
    """One zone's content, fully pre-formatted to strings the template just interpolates.

    Built by `_build_zone_views` from a `ZoneMetadata`, that zone's
    obstruction-event counts, and (when available) its `ZoneNote` from the
    LLM. Every `_line`/`_lines` field is `None`/empty exactly when that
    zone has nothing to say for that section, so the template can test for
    absence with a plain `{% if %}` rather than re-deriving the condition.
    """

    zone_id: int
    zone_name: str
    status_label: str
    flags_label: str
    observation_lines: list[str]
    env_line: str
    obstruction_line: str | None
    recommendation_line: str | None
    image_note: str | None
    growth_note_ko: str | None
    visual_finding_lines: list[str]


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
    """A deterministic recommendation, independent of whether the LLM ran: the
    only rule-derivable recommendation is "recapture a flagged zone" — this
    always shows when a zone is flagged, regardless of `llm`, per hard rule 1
    (this is a rule-based fact, not model judgement). `None` when the zone
    has no flags at all.
    """
    if "재촬영_필요" not in zone.flags:
        return None
    rate_pct = round(zone.undetermined_rate * 100) if zone.undetermined_rate is not None else 0
    return f"{zone.zone_id}구역: 재촬영 권장 (판단불가 비율 {rate_pct}%)"


def _image_note(zone: ZoneMetadata) -> str | None:
    """"이미지 없음" when `pipeline/select_images.py` selected nothing for this
    zone — spec §7: "If a zone has no eligible images, it contributes
    text-only and the report notes 이미지 없음." True whether the zone had
    no captured images at all, every image fell below the quality floor, or
    (before A4 populates `image_ids`) selection simply hasn't run yet.
    """
    return None if zone.image_ids else "이미지 없음"


def _env_note_suffix(zone_id: int, llm: LlmReportOutput | None, notes_by_zone: dict) -> str:
    """`" — {env_note_ko}"` when the LLM ran and has a note for this zone, else `""`.

    Matches spec §10's own template example exactly: `... (표본 {{ n }}개)
    {% if llm %} — {{ llm.zone(z.zone_id).env_note_ko }}{% endif %}` — same
    "em dash" separator on the same line as the deterministic env stats.
    """
    if llm is None:
        return ""
    note = notes_by_zone.get(zone_id)
    return f" — {note.env_note_ko}" if note else ""


def _build_zone_views(
    agg: PatrolAggregate, obstructions: dict[int, dict[str, int]], llm: LlmReportOutput | None
) -> list[ZoneView]:
    """Turn `agg.zones` into fully pre-formatted `ZoneView`s. See module docstring for why."""
    notes_by_zone = {note.zone_id: note for note in llm.zones} if llm else {}

    views = []
    for zone in agg.zones:
        note = notes_by_zone.get(zone.zone_id)
        views.append(
            ZoneView(
                zone_id=zone.zone_id,
                zone_name=zone.zone_name,
                status_label=zone.status.value,
                flags_label=f" ({', '.join(zone.flags)})" if zone.flags else "",
                observation_lines=_observation_lines(zone),
                env_line=_env_line(zone) + _env_note_suffix(zone.zone_id, llm, notes_by_zone),
                obstruction_line=_obstruction_line(zone.zone_id, obstructions[zone.zone_id])
                if zone.zone_id in obstructions
                else None,
                recommendation_line=_recommendation_line(zone),
                image_note=_image_note(zone),
                growth_note_ko=note.growth_note_ko if note else None,
                visual_finding_lines=[f"- {f}" for f in note.visual_findings_ko] if note else [],
            )
        )
    return views


def _llm_recommendation_lines(agg: PatrolAggregate, llm: LlmReportOutput | None) -> list[str]:
    """`"{zone_id}구역: {action}"` for every `recommended_actions_ko` entry on
    every zone note that matches a real `agg` zone — flattened across zones
    for the 권장 조치 section, alongside (not replacing) the deterministic
    recapture recommendations `_recommendation_line` always produces.
    """
    if llm is None:
        return []
    valid_zone_ids = {z.zone_id for z in agg.zones}
    lines = []
    for note in llm.zones:
        if note.zone_id not in valid_zone_ids:
            continue
        for action in note.recommended_actions_ko:
            lines.append(f"{note.zone_id}구역: {action}")
    return lines


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
    obstructions: dict[int, dict[str, int]],
    llm: LlmReportOutput | None = None,
    coverage_warn_threshold: float = 0.90,
) -> str:
    """Render `report.md.j2` to a Markdown string. Pure — no I/O, no network.

    `agg` supplies every number and status, pre-formatted per zone by
    `_build_zone_views` before the template ever runs. `obstructions` is
    the per-zone `EMERGENCY_STOP`/`LINE_LOST` count `agg` doesn't carry —
    normally `PatrolSegmentation.obstruction_counts()` from the pipeline's
    own segmentation, or `Payload.obstructions` when rendering from a
    stored payload with no segmentation available at all (A6's
    `cli.py regenerate`, which has no rover or database access — see that
    command). This function takes the plain dict rather than a
    `PatrolSegmentation` object for exactly that reason: regeneration is
    the reason this dependency was narrowed from "the whole segmentation"
    to "the one derived value this function actually uses."

    `llm` is the (possibly already zone-filtered) structured output from
    `llm/client.py::generate_report`, or `None` on any A5 fallback — the
    template's `{% if llm %}` branches handle that case explicitly, and
    every deterministic section (observation counts, recapture
    recommendations, coverage/confidence limitations) renders identically
    whether or not `llm` is present, per hard rule 1: LLM content is always
    additive prose next to a figure, never a replacement for one.
    `coverage_warn_threshold` defaults to `config.COVERAGE_WARN_THRESHOLD`'s
    value but is passed as a plain float rather than a `Settings` object,
    keeping this function's dependency surface to exactly what it renders.
    """
    zone_views = _build_zone_views(agg, obstructions, llm)

    template = _jinja_env().get_template("report.md.j2")
    return template.render(
        agg=agg,
        llm=llm,
        zone_views=zone_views,
        obstruction_lines=[zv.obstruction_line for zv in zone_views if zv.obstruction_line],
        recommendation_lines=[zv.recommendation_line for zv in zone_views if zv.recommendation_line],
        llm_recommendation_lines=_llm_recommendation_lines(agg, llm),
        coverage_warn_threshold=coverage_warn_threshold,
    )
