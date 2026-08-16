from __future__ import annotations

import re

from ai_report.models import (
    DataCompleteness,
    LlmMetadata,
    PatrolAggregate,
    ReportStatus,
    StatSummary,
    ZoneEnv,
    ZoneMetadata,
)
from ai_report.pipeline.segment import PatrolSegmentation, ZoneWindow
from ai_report.render.markdown import render_report

PATROL_ID = "20260813_1430"

# ICD §C3.2 — these six H2 headings, in this order, always.
_EXPECTED_SECTIONS = [
    "## 순찰 요약",
    "## 구역별 생육 현황",
    "## 환경 조건",
    "## 통로 장애 요인",
    "## 권장 조치",
    "## 데이터 한계",
]


def make_aggregate(zones: list[ZoneMetadata], overall_status: ReportStatus = ReportStatus.NORMAL) -> PatrolAggregate:
    return PatrolAggregate(
        patrol_id=PATROL_ID,
        patrol_date="2026-08-13",
        duration_min=18,
        overall_status=overall_status,
        llm=LlmMetadata(enabled=False),
        data_completeness=DataCompleteness(
            udp_received=100, udp_expected=100, rate=1.0, images_analysed=5, zone_boundary_confidence="high"
        ),
        zones=zones,
    )


def make_segmentation(windows: list[ZoneWindow] | None = None) -> PatrolSegmentation:
    return PatrolSegmentation(
        patrol_id=PATROL_ID, boundary_confidence="high", windows=windows or [],
        patrol_start_ts_ms=0, patrol_end_ts_ms=60_000,
    )


def one_zone(zone_id: int = 1, flags: list[str] | None = None, undetermined_rate: float | None = 0.1) -> ZoneMetadata:
    return ZoneMetadata(
        zone_id=zone_id,
        zone_name=f"{zone_id}구역",
        status=ReportStatus.NORMAL,
        env=ZoneEnv(temp_c=StatSummary(avg=27.0, min=26.0, max=28.0, n=10), humid_pct=StatSummary(avg=60.0, min=55.0, max=65.0, n=10)),
        observations={"tomato": {"정상": 8, "미성숙": 2}},
        undetermined_rate=undetermined_rate,
        flags=flags or [],
        image_ids=[],
        confidence="high",
    )


def section_order(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def test_six_sections_present_and_ordered():
    agg = make_aggregate([one_zone()])
    md = render_report(agg, make_segmentation())
    assert section_order(md) == _EXPECTED_SECTIONS


def test_six_sections_present_with_zero_zones():
    """A3 acceptance: report generates successfully for a patrol with zero zones."""
    agg = make_aggregate([])
    md = render_report(agg, make_segmentation())
    assert section_order(md) == _EXPECTED_SECTIONS
    assert "구역 정보 없음" in md
    assert "환경 데이터 없음" in md


def test_six_sections_present_with_single_zone():
    agg = make_aggregate([one_zone(1)])
    md = render_report(agg, make_segmentation())
    assert section_order(md) == _EXPECTED_SECTIONS
    assert "### 1구역 — 1구역" in md


def test_numbers_trace_to_aggregate_not_hardcoded():
    agg = make_aggregate([one_zone(1)], overall_status=ReportStatus.CAUTION)
    md = render_report(agg, make_segmentation())
    assert "**주의**" in md  # overall_status.value, not the raw ReportStatus.CAUTION repr
    assert "ReportStatus" not in md  # would appear if .value were forgotten anywhere
    assert "18분" in md  # duration_min
    assert "100.0%" in md  # data_completeness.rate * 100


def test_observation_counts_appear_as_table_rows():
    agg = make_aggregate([one_zone(1)])
    md = render_report(agg, make_segmentation())
    assert "| tomato | 정상 | 8 |" in md
    assert "| tomato | 미성숙 | 2 |" in md


def test_zone_with_no_observations_says_so_not_empty_table():
    zone = one_zone(1)
    zone.observations = {}
    agg = make_aggregate([zone])
    md = render_report(agg, make_segmentation())
    assert "관측 없음" in md


def test_llm_disabled_uses_fallback_summary_and_states_limitation():
    agg = make_aggregate([one_zone()])
    md = render_report(agg, make_segmentation())
    assert "LLM 분석이 포함되지 않은 자동 생성 리포트입니다." in md
    # stated as a limitation too, per spec's data-limitations example
    assert md.count("LLM 분석이 포함되지 않은") >= 2


def test_low_boundary_confidence_stated_in_data_limitations():
    # A real aggregate() always sets data_completeness.zone_boundary_confidence
    # from segmentation.boundary_confidence, so keep both consistent here too.
    agg = make_aggregate([one_zone()])
    agg.data_completeness.zone_boundary_confidence = "low"
    seg = PatrolSegmentation(patrol_id=PATROL_ID, boundary_confidence="low", windows=[], patrol_start_ts_ms=0, patrol_end_ts_ms=1000)
    md = render_report(agg, seg)
    assert "추정값" in md


def test_low_coverage_triggers_warning_in_data_limitations():
    agg = make_aggregate([one_zone()])
    agg.data_completeness.rate = 0.5
    md = render_report(agg, make_segmentation(), coverage_warn_threshold=0.90)
    assert "90% 미만" in md


def test_high_coverage_does_not_trigger_warning():
    agg = make_aggregate([one_zone()])
    agg.data_completeness.rate = 0.99
    md = render_report(agg, make_segmentation(), coverage_warn_threshold=0.90)
    assert "미만입니다" not in md


def test_recapture_flag_produces_recommendation():
    agg = make_aggregate([one_zone(1, flags=["재촬영_필요"], undetermined_rate=0.42)])
    md = render_report(agg, make_segmentation())
    assert "1구역: 재촬영 권장 (판단불가 비율 42%)" in md


def test_no_flags_produces_default_recommendation_text():
    agg = make_aggregate([one_zone(1, flags=[])])
    md = render_report(agg, make_segmentation())
    assert "현재 특별한 조치가 필요한 구역이 없습니다." in md


def test_obstruction_events_listed_per_zone():
    from ai_report.models import EventMessage, EventType

    zone_window = ZoneWindow(
        zone_id=1, start_ts_ms=0, end_ts_ms=1000, telemetry=[], analysis=[],
        events=[
            EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=100, type=EventType.EMERGENCY_STOP, detail={"ultra_cm": 8}),
            EventMessage(patrol_id=PATROL_ID, event_seq=1, ts_ms=200, type=EventType.EMERGENCY_STOP, detail={"ultra_cm": 5}),
            EventMessage(patrol_id=PATROL_ID, event_seq=2, ts_ms=300, type=EventType.LINE_LOST, detail={"duration_ms": 1000}),
        ],
    )
    agg = make_aggregate([one_zone(1)])
    seg = make_segmentation([zone_window])
    md = render_report(agg, seg)
    assert "1구역: EMERGENCY_STOP 2회, LINE_LOST 1회" in md


def test_no_obstruction_events_states_none_recorded():
    agg = make_aggregate([one_zone()])
    md = render_report(agg, make_segmentation())
    assert "통로 장애 이벤트가 기록되지 않았습니다." in md


def test_no_markdown_line_is_a_run_on_of_multiple_zones():
    """Regression test for the trim_blocks whitespace bug found during
    development: a content line ending in a block tag lost its newline,
    concatenating every zone's env line onto one unreadable run of text.
    """
    agg = make_aggregate([one_zone(1), one_zone(2), one_zone(3)])
    md = render_report(agg, make_segmentation())
    env_lines = [line for line in md.splitlines() if re.match(r"^- \d+구역: ", line)]
    assert len(env_lines) == 3  # one line per zone, not one line total
