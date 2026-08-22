from __future__ import annotations

import re

from ai_report.llm.schema import LlmReportOutput, ZoneNote
from ai_report.models import (
    DataCompleteness,
    EventMessage,
    EventType,
    LlmMetadata,
    PatrolAggregate,
    ReportStatus,
    StatSummary,
    ZoneEnv,
    ZoneMetadata,
)
from ai_report.pipeline.segment import PatrolSegmentation, ZoneWindow
from ai_report.render.markdown import crop_status_advisory, render_report

PATROL_ID = "20260813_1430"


def make_llm_output(zones: list[ZoneNote] | None = None, **overrides) -> LlmReportOutput:
    defaults = {
        "summary_ko": "요약입니다.",
        "overall_note_ko": "종합 소견입니다.",
        "zones": zones or [],
        "path_obstructions_ko": [],
        "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "다음 순찰 제안입니다.",
    }
    defaults.update(overrides)
    return LlmReportOutput.model_validate(defaults)


def make_zone_note(zone_id: int = 1, **overrides) -> ZoneNote:
    defaults = {
        "zone_id": zone_id,
        "growth_note_ko": "생육 소견입니다.",
        "env_note_ko": "환경 소견입니다.",
        "visual_findings_ko": ["시각 소견1"],
        "recommended_actions_ko": ["권장 조치1"],
    }
    defaults.update(overrides)
    return ZoneNote.model_validate(defaults)

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


def one_zone(
    zone_id: int = 1,
    flags: list[str] | None = None,
    undetermined_rate: float | None = 0.1,
    image_ids: list[str] | None = None,
) -> ZoneMetadata:
    return ZoneMetadata(
        zone_id=zone_id,
        zone_name=f"{zone_id}구역",
        status=ReportStatus.NORMAL,
        env=ZoneEnv(temp_c=StatSummary(avg=27.0, min=26.0, max=28.0, n=10), humid_pct=StatSummary(avg=60.0, min=55.0, max=65.0, n=10)),
        observations={"tomato": {"정상": 8, "미성숙": 2}},
        undetermined_rate=undetermined_rate,
        flags=flags or [],
        image_ids=image_ids or [],
        confidence="high",
    )


def section_order(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def test_six_sections_present_and_ordered():
    agg = make_aggregate([one_zone()])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert section_order(md) == _EXPECTED_SECTIONS


def test_six_sections_present_with_zero_zones():
    """A3 acceptance: report generates successfully for a patrol with zero zones."""
    agg = make_aggregate([])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert section_order(md) == _EXPECTED_SECTIONS
    assert "구역 정보 없음" in md
    assert "환경 데이터 없음" in md


def test_six_sections_present_with_single_zone():
    agg = make_aggregate([one_zone(1)])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert section_order(md) == _EXPECTED_SECTIONS
    assert "### 1구역 — 1구역" in md


def test_numbers_trace_to_aggregate_not_hardcoded():
    agg = make_aggregate([one_zone(1)], overall_status=ReportStatus.CAUTION)
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "**주의**" in md  # overall_status.value, not the raw ReportStatus.CAUTION repr
    assert "ReportStatus" not in md  # would appear if .value were forgotten anywhere
    assert "18분" in md  # duration_min
    assert "100.0%" in md  # data_completeness.rate * 100


def test_observation_counts_appear_as_table_rows():
    agg = make_aggregate([one_zone(1)])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "| tomato | 정상 | 8 |" in md
    assert "| tomato | 미성숙 | 2 |" in md


def test_zone_with_no_observations_says_so_not_empty_table():
    zone = one_zone(1)
    zone.observations = {}
    agg = make_aggregate([zone])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "관측 없음" in md


def test_llm_disabled_uses_fallback_summary_and_states_limitation():
    agg = make_aggregate([one_zone()])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "LLM 분석이 포함되지 않은 자동 생성 리포트입니다." in md
    # stated as a limitation too, per spec's data-limitations example
    assert md.count("LLM 분석이 포함되지 않은") >= 2


def test_low_boundary_confidence_stated_in_data_limitations():
    # A real aggregate() always sets data_completeness.zone_boundary_confidence
    # from segmentation.boundary_confidence, so keep both consistent here too.
    agg = make_aggregate([one_zone()])
    agg.data_completeness.zone_boundary_confidence = "low"
    seg = PatrolSegmentation(patrol_id=PATROL_ID, boundary_confidence="low", windows=[], patrol_start_ts_ms=0, patrol_end_ts_ms=1000)
    md = render_report(agg, seg.obstruction_counts())
    assert "추정값" in md


def test_low_coverage_triggers_warning_in_data_limitations():
    agg = make_aggregate([one_zone()])
    agg.data_completeness.rate = 0.5
    md = render_report(agg, make_segmentation().obstruction_counts(), coverage_warn_threshold=0.90)
    assert "90% 미만" in md


def test_high_coverage_does_not_trigger_warning():
    agg = make_aggregate([one_zone()])
    agg.data_completeness.rate = 0.99
    md = render_report(agg, make_segmentation().obstruction_counts(), coverage_warn_threshold=0.90)
    assert "미만입니다" not in md


def test_recapture_flag_produces_recommendation():
    agg = make_aggregate([one_zone(1, flags=["재촬영_필요"], undetermined_rate=0.42)])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "1구역: 재촬영 권장 (판단불가 비율 42%)" in md


def test_no_flags_produces_default_recommendation_text():
    agg = make_aggregate([one_zone(1, flags=[])])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "현재 특별한 조치가 필요한 구역이 없습니다." in md


def test_obstruction_events_listed_per_zone():
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
    md = render_report(agg, seg.obstruction_counts())
    assert "1구역: EMERGENCY_STOP 2회, LINE_LOST 1회" in md


def test_no_obstruction_events_states_none_recorded():
    agg = make_aggregate([one_zone()])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "통로 장애 이벤트가 기록되지 않았습니다." in md


def test_no_markdown_line_is_a_run_on_of_multiple_zones():
    """Regression test for the trim_blocks whitespace bug found during
    development: a content line ending in a block tag lost its newline,
    concatenating every zone's env line onto one unreadable run of text.
    """
    agg = make_aggregate([one_zone(1), one_zone(2), one_zone(3)])
    md = render_report(agg, make_segmentation().obstruction_counts())
    env_lines = [line for line in md.splitlines() if re.match(r"^- \d+구역: ", line)]
    assert len(env_lines) == 3  # one line per zone, not one line total


def test_zone_with_no_selected_images_shows_image_note():
    agg = make_aggregate([one_zone(1, image_ids=[])])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "이미지 없음" in md


def test_zone_with_selected_images_omits_image_note():
    agg = make_aggregate([one_zone(1, image_ids=["z1_003", "z1_007"])])
    md = render_report(agg, make_segmentation().obstruction_counts())
    assert "이미지 없음" not in md


def test_llm_summary_and_overall_note_appear_in_patrol_summary():
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(summary_ko="요약 문장.", overall_note_ko="종합 문장.")
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    assert "요약 문장." in md
    assert "종합 문장." in md


def test_llm_growth_note_and_visual_findings_appear_per_zone():
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(zones=[make_zone_note(1, growth_note_ko="생육 특이사항.", visual_findings_ko=["잎 색상 양호"])])
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    assert "생육 특이사항." in md
    assert "- 잎 색상 양호" in md


def test_llm_env_note_appended_to_env_line_with_em_dash():
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(zones=[make_zone_note(1, env_note_ko="온습도 안정적.")])
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    env_line = next(line for line in md.splitlines() if line.startswith("- 1구역: "))
    assert env_line.endswith("— 온습도 안정적.")


def test_llm_path_obstructions_appended_alongside_deterministic_bullets():
    zone_window = ZoneWindow(
        zone_id=1, start_ts_ms=0, end_ts_ms=1000, telemetry=[], analysis=[],
        events=[EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=100, type=EventType.EMERGENCY_STOP)],
    )
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(path_obstructions_ko=["1구역에서 비상정지가 함께 관찰됨."])
    md = render_report(agg, make_segmentation([zone_window]).obstruction_counts(), llm=llm)
    assert "1구역: EMERGENCY_STOP 1회" in md  # deterministic bullet still present
    assert "1구역에서 비상정지가 함께 관찰됨." in md  # LLM prose appended


def test_llm_recommended_actions_appended_with_zone_prefix():
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(zones=[make_zone_note(1, recommended_actions_ko=["정기 관수 유지"])])
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    assert "1구역: 정기 관수 유지" in md


def test_llm_next_patrol_suggestion_appears_in_recommendations():
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(next_patrol_suggestion_ko="다음 순찰은 오전에 권장됩니다.")
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    assert "다음 순찰은 오전에 권장됩니다." in md


def test_llm_data_limitations_appended_alongside_deterministic_bullets():
    agg = make_aggregate([one_zone(1)])
    llm = make_llm_output(data_limitations_ko=["일부 이미지 품질이 낮았습니다."])
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    assert "UDP 패킷 수신" in md  # deterministic bullet still present
    assert "일부 이미지 품질이 낮았습니다." in md


def test_llm_note_for_unknown_zone_id_produces_no_content():
    """The zone_id-dropping itself is llm/client.py's job (spec §9); this
    proves render_report is independently robust even if unfiltered LLM
    output reaches it — an orphaned zone note just has nothing to attach to.
    """
    agg = make_aggregate([one_zone(1)])  # only zone 1 exists in the aggregate
    llm = make_llm_output(zones=[make_zone_note(99, growth_note_ko="존재하지 않는 구역 소견")])
    md = render_report(agg, make_segmentation().obstruction_counts(), llm=llm)
    assert "존재하지 않는 구역 소견" not in md


def test_deterministic_sections_identical_with_and_without_llm():
    """Hard rule 1: LLM content is always additive, never a replacement —
    every deterministic figure/status must render identically whether or
    not llm is provided.
    """
    agg = make_aggregate([one_zone(1)], overall_status=ReportStatus.CAUTION)
    without_llm = render_report(agg, make_segmentation().obstruction_counts())
    with_llm = render_report(agg, make_segmentation().obstruction_counts(), llm=make_llm_output(zones=[make_zone_note(1)]))
    assert "**주의**" in without_llm
    assert "**주의**" in with_llm
    assert "| tomato | 정상 | 8 |" in without_llm
    assert "| tomato | 정상 | 8 |" in with_llm


def test_crop_status_advisory_normal_picks_object_particle_by_batchim():
    # 당근 ends in a syllable with a final consonant (batchim) -> 을
    assert crop_status_advisory("정상", "당근") == "농작물 당근을 수확하세요"
    # 상추 ends in a syllable with no final consonant -> 를
    assert crop_status_advisory("정상", "상추") == "농작물 상추를 수확하세요"


def test_crop_status_advisory_wilted_and_pest():
    assert crop_status_advisory("시듦", "상추") == "농작물 상추에 급수를 공급하세요"
    assert crop_status_advisory("병충해", "고추") == "농작물 고추에 약을 살포하세요"


def test_crop_status_advisory_unknown_status_returns_none():
    # Real CropState values are deliberately not part of this rule table.
    assert crop_status_advisory("미성숙", "상추") is None
    assert crop_status_advisory("판단불가", "상추") is None
    assert crop_status_advisory("병충해_의심", "상추") is None
