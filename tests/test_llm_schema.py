from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_report.llm.schema import LlmReportOutput, ZoneNote, output_json_schema


def valid_output_kwargs() -> dict:
    return {
        "summary_ko": "요약",
        "overall_note_ko": "종합 소견",
        "zones": [
            {
                "zone_id": 1,
                "growth_note_ko": "생육 소견",
                "env_note_ko": "환경 소견",
                "visual_findings_ko": ["소견1"],
                "recommended_actions_ko": ["조치1"],
            }
        ],
        "path_obstructions_ko": ["장애 소견"],
        "data_limitations_ko": ["한계 소견"],
        "next_patrol_suggestion_ko": "다음 순찰 제안",
    }


def test_valid_output_parses():
    output = LlmReportOutput.model_validate(valid_output_kwargs())
    assert output.zones[0].zone_id == 1


def test_missing_field_raises():
    data = valid_output_kwargs()
    del data["summary_ko"]
    with pytest.raises(ValidationError):
        LlmReportOutput.model_validate(data)


def test_extra_field_raises():
    data = valid_output_kwargs()
    data["unexpected_field"] = "x"
    with pytest.raises(ValidationError):
        LlmReportOutput.model_validate(data)


def test_zone_note_missing_field_raises():
    with pytest.raises(ValidationError):
        ZoneNote.model_validate({"zone_id": 1, "growth_note_ko": "x"})


def _walk_object_schemas(schema: dict):
    """Yield every object-typed (sub)schema in `schema`, including $defs."""
    if schema.get("type") == "object":
        yield schema
    for sub in schema.get("properties", {}).values():
        yield from _walk_object_schemas(sub)
    if "items" in schema:
        yield from _walk_object_schemas(schema["items"])
    for sub in schema.get("$defs", {}).values():
        yield from _walk_object_schemas(sub)


def test_schema_is_strict_at_every_level():
    """OpenAI structured-output strict mode: every object needs
    additionalProperties: false and every property listed in required.
    """
    schema = output_json_schema()
    object_schemas = list(_walk_object_schemas(schema))
    assert len(object_schemas) >= 2  # LlmReportOutput itself + nested ZoneNote
    for obj_schema in object_schemas:
        assert obj_schema["additionalProperties"] is False
        assert set(obj_schema["required"]) == set(obj_schema["properties"].keys())


def test_schema_has_no_numeric_fields_except_zone_id():
    """Hard rule 1, structurally: nowhere in the schema for the model to
    return a number that isn't the zone_id it's echoing back to identify
    which zone a note belongs to.
    """
    schema = output_json_schema()
    for obj_schema in _walk_object_schemas(schema):
        for name, prop in obj_schema["properties"].items():
            if name == "zone_id":
                assert prop["type"] == "integer"
                continue
            if name == "zones":
                assert prop["type"] == "array"
                assert prop["items"] == {"$ref": "#/$defs/ZoneNote"}  # objects, not a numeric leaf
                continue
            prop_type = prop.get("type") or prop.get("items", {}).get("type")
            assert prop_type in ("string", "array"), f"{name} has non-string/array type {prop_type!r}"
            if prop_type == "array":
                assert prop["items"]["type"] == "string"
