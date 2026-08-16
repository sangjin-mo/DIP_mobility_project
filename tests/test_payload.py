from __future__ import annotations

import json
from pathlib import Path

from ai_report.config import get_settings
from ai_report.models import (
    DataCompleteness,
    EventMessage,
    EventType,
    LlmMetadata,
    PatrolAggregate,
    ReportStatus,
    ZoneEnv,
    ZoneMetadata,
)
from ai_report.pipeline.payload import build_payload, estimate_tokens, write_payload
from ai_report.pipeline.segment import PatrolSegmentation, ZoneWindow

PATROL_ID = "20260813_1430"


def zone(zone_id: int, image_ids: list[str]) -> ZoneMetadata:
    return ZoneMetadata(
        zone_id=zone_id, zone_name=f"{zone_id}구역", status=ReportStatus.NORMAL, env=ZoneEnv(),
        observations={}, undetermined_rate=0.0, flags=[], image_ids=image_ids, confidence="high",
    )


def make_agg(zones: list[ZoneMetadata]) -> PatrolAggregate:
    return PatrolAggregate(
        patrol_id=PATROL_ID, patrol_date="2026-08-13", duration_min=20, overall_status=ReportStatus.NORMAL,
        llm=LlmMetadata(enabled=False),
        data_completeness=DataCompleteness(udp_received=1, udp_expected=1, rate=1.0, images_analysed=0, zone_boundary_confidence="high"),
        zones=zones,
    )


def make_segmentation(windows: list[ZoneWindow] | None = None) -> PatrolSegmentation:
    return PatrolSegmentation(patrol_id=PATROL_ID, boundary_confidence="high", windows=windows or [], patrol_start_ts_ms=0, patrol_end_ts_ms=60_000)


def test_estimate_tokens_matches_spec_formula():
    settings = get_settings()
    tokens = estimate_tokens(num_zones=6, total_images=18, settings=settings)
    expected = settings.TOKEN_ESTIMATE_SYSTEM_PROMPT + settings.TOKEN_ESTIMATE_FIXED + 6 * settings.TOKEN_ESTIMATE_PER_ZONE + 18 * settings.TOKEN_ESTIMATE_PER_IMAGE
    assert tokens == expected
    assert tokens == 700 + 300 + 1200 + 13770  # spec §8's own worked example, ~16000


def test_under_budget_keeps_all_selected_images():
    agg = make_agg([zone(1, ["a", "b", "c"])])
    payload, _tokens = build_payload(agg, make_segmentation(), get_settings())
    assert payload.zones[0].image_ids == ["a", "b", "c"]
    assert payload.known_limitations == []


def test_over_budget_degrades_to_two_images_per_zone():
    # 6 zones: 3 images/zone estimates to 15,970 tokens; 2 images/zone to
    # 11,380. A budget of 12,000 accepts the "2" step but not "3".
    settings = get_settings().model_copy(update={"LLM_MAX_INPUT_TOKENS": 12_000})
    zones = [zone(i, ["a", "b", "c"]) for i in range(1, 7)]  # 6 zones x 3 images
    agg = make_agg(zones)
    payload, tokens = build_payload(agg, make_segmentation(), settings)
    assert all(len(z.image_ids) == 2 for z in payload.zones)
    assert tokens <= settings.LLM_MAX_INPUT_TOKENS
    assert any("2장" in note for note in payload.known_limitations)


def test_extreme_over_budget_falls_back_to_text_only():
    settings = get_settings().model_copy(update={"LLM_MAX_INPUT_TOKENS": 100})
    zones = [zone(i, ["a", "b", "c"]) for i in range(1, 7)]
    agg = make_agg(zones)
    payload, tokens = build_payload(agg, make_segmentation(), settings)
    assert all(z.image_ids == [] for z in payload.zones)
    assert any("텍스트" in note for note in payload.known_limitations)
    # text-only is accepted as final fallback even though still over budget
    assert tokens > settings.LLM_MAX_INPUT_TOKENS


def test_image_priority_order_preserved_when_truncating():
    agg = make_agg([zone(1, ["anomaly", "normal", "undetermined"])])
    settings = get_settings().model_copy(update={"LLM_MAX_INPUT_TOKENS": 2000})
    payload, _ = build_payload(agg, make_segmentation(), settings)
    # truncation keeps the highest-priority (first) entries, not a re-selection
    assert payload.zones[0].image_ids == ["anomaly", "normal"][: len(payload.zones[0].image_ids)]


def test_obstructions_included_from_segmentation():
    window = ZoneWindow(
        zone_id=1, start_ts_ms=0, end_ts_ms=1000, telemetry=[], analysis=[],
        events=[EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=100, type=EventType.EMERGENCY_STOP)],
    )
    agg = make_agg([zone(1, [])])
    payload, _ = build_payload(agg, make_segmentation([window]), get_settings())
    assert payload.obstructions == {1: {"EMERGENCY_STOP": 1}}


def test_prompt_version_recorded_from_settings():
    settings = get_settings().model_copy(update={"PROMPT_VERSION": "v9.9"})
    agg = make_agg([])
    payload, _ = build_payload(agg, make_segmentation(), settings)
    assert payload.prompt_version == "v9.9"


def test_payload_excludes_llm_block():
    agg = make_agg([])
    payload, _ = build_payload(agg, make_segmentation(), get_settings())
    assert not hasattr(payload, "llm")


def test_payload_is_complete_enough_to_regenerate_without_db_access(tmp_path: Path):
    """A4 acceptance: payload.json must be complete enough to regenerate a
    report with no database access — i.e. it must carry everything
    render_report needs beyond what metadata.json alone provides.
    """
    window = ZoneWindow(
        zone_id=1, start_ts_ms=0, end_ts_ms=1000, telemetry=[], analysis=[],
        events=[EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=100, type=EventType.LINE_LOST, detail={"duration_ms": 500})],
    )
    agg = make_agg([zone(1, ["a"])])
    payload, _ = build_payload(agg, make_segmentation([window]), get_settings())

    out = write_payload(payload, tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))

    # Everything render_report needs is here: zone stats/status (from
    # metadata-shaped fields) plus obstructions (not in metadata.json).
    assert data["zones"][0]["zone_id"] == 1
    assert data["obstructions"] == {"1": {"LINE_LOST": 1}}
    assert "llm" not in data


def test_write_payload_creates_valid_json_file(tmp_path: Path):
    agg = make_agg([zone(1, ["a"])])
    payload, _ = build_payload(agg, make_segmentation(), get_settings())
    out = write_payload(payload, tmp_path)
    assert out == tmp_path / "payload.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["patrol_id"] == PATROL_ID
