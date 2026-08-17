"""Structured LLM output — spec §9's output schema, verbatim in shape.

`LlmReportOutput` is what the model must return, enforced by the OpenAI
API's own strict structured-output mode (not just validated after the
fact): every field is mandatory (no field has a default), matching spec
§9's note that "strict mode requires every property listed in `required`
and `additionalProperties: false` at every level."

Deliberately contains **no numeric field anywhere** — every field is a
Korean string or a list of Korean strings. This is what makes GUIDELINES.md
hard rule 1 ("the LLM never computes numbers") a structural guarantee
rather than a hope: the model cannot return a number through this schema
even if the prompt were silent on the matter, because there is nowhere in
the shape for one to go.

Called by:
- `llm/client.py::generate_report` — passes `output_json_schema()` as the
  API request's `response_format`, and parses the response body into
  `LlmReportOutput.model_validate_json(...)`.
- `render/markdown.py::render_report` — consumes a (possibly
  zone-filtered) `LlmReportOutput` to fill the LLM-authored parts of the report.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ZoneNote(BaseModel):
    """One zone's LLM-authored prose (spec §9's `zones[]` entry).

    `zone_id` is how `llm/client.py::_drop_unknown_zones` matches this
    back to a real `ZoneMetadata` — a `zone_id` the aggregate doesn't
    recognise is dropped and logged (A5 acceptance criterion), never
    rendered.
    """

    model_config = ConfigDict(extra="forbid")

    zone_id: int
    growth_note_ko: str
    env_note_ko: str
    visual_findings_ko: list[str]
    recommended_actions_ko: list[str]


class LlmReportOutput(BaseModel):
    """The complete structured response for one report (spec §9).

    `summary_ko`/`overall_note_ko` are patrol-level; everything else is
    either per-zone (`zones`) or patrol-level lists
    (`path_obstructions_ko`, `data_limitations_ko`,
    `next_patrol_suggestion_ko`). None of this replaces a number or a
    status value anywhere — `render/markdown.py` only ever drops these
    strings into slots next to figures that came from `PatrolAggregate`.
    """

    model_config = ConfigDict(extra="forbid")

    summary_ko: str
    overall_note_ko: str
    zones: list[ZoneNote]
    path_obstructions_ko: list[str]
    data_limitations_ko: list[str]
    next_patrol_suggestion_ko: str


def _make_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively force `additionalProperties: false` and a complete
    `required` list onto every object-typed (sub)schema.

    Pydantic's `model_json_schema()` already produces this for models
    built the way `ZoneNote`/`LlmReportOutput` are (every field mandatory,
    `extra="forbid"`) — this function exists as a defensive, explicit
    guarantee of the OpenAI strict-mode contract rather than an assumption
    that stays true only as long as nobody adds an optional field later.
    Called only by `output_json_schema`.
    """
    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())
        for prop_schema in schema["properties"].values():
            _make_strict(prop_schema)
    if "items" in schema:
        _make_strict(schema["items"])
    for key in ("$defs", "definitions"):
        for sub_schema in schema.get(key, {}).values():
            _make_strict(sub_schema)
    return schema


def output_json_schema() -> dict[str, Any]:
    """The strict JSON schema for the OpenAI structured-output API's `response_format`.

    Called by `llm/client.py::generate_report` to build the API request.
    """
    return _make_strict(LlmReportOutput.model_json_schema())
