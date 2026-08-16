from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_report.config import get_settings
from ai_report.models import (
    AnalysisResult,
    CropState,
    Detection,
    DriveReading,
    DriveState,
    EnvReading,
    EventMessage,
    EventType,
    ReportStatus,
    TelemetryPacket,
)
from ai_report.pipeline.aggregate import aggregate
from ai_report.pipeline.segment import ZoneWindow, segment_patrol

PATROL_ID = "20260813_1430"


def telemetry(seq: int, ts_ms: int, temp_c: float | None = 25.0, humid_pct: float | None = 60.0) -> TelemetryPacket:
    return TelemetryPacket(
        patrol_id=PATROL_ID,
        seq=seq,
        ts_ms=ts_ms,
        type="TELEMETRY",
        zone_id=None,
        env=EnvReading(temp_c=temp_c, humid_pct=humid_pct),
        drive=DriveReading(speed_mps=0.3, steer=0.0, ultra_cm=100, state=DriveState.RUNNING),
    )


def detection(state: CropState, count: int, confidence: float | None = 0.9) -> Detection:
    return Detection.model_validate({"class": "tomato", "state": state.value, "count": count, "confidence": confidence})


def analysis(image_id: str, ts_ms: int, detections: list[Detection]) -> AnalysisResult:
    return AnalysisResult(
        image_id=image_id,
        patrol_id=PATROL_ID,
        captured_at_ms=ts_ms,
        image_path=f"images/{PATROL_ID}/{image_id}.jpg",
        image_quality=0.8,
        detections=detections,
    )


def zone_window(zone_id: int, detections: list[Detection], events: list[EventMessage] | None = None) -> ZoneWindow:
    return ZoneWindow(
        zone_id=zone_id,
        start_ts_ms=0,
        end_ts_ms=1000,
        telemetry=[telemetry(0, 0)],
        analysis=[analysis(f"z{zone_id}_000", 0, detections)] if detections else [],
        events=events or [],
    )


class FakeSegmentation:
    """Minimal stand-in for `PatrolSegmentation` — just enough surface for
    `aggregate()`: `.zones()`, `.windows`, `.boundary_confidence`,
    `.patrol_start_ts_ms`/`.patrol_end_ts_ms`. Avoids going through
    `segment_patrol` for tests that only care about the aggregation math,
    not segmentation.
    """

    def __init__(self, windows, boundary_confidence="high", patrol_start_ts_ms=0, patrol_end_ts_ms=60_000):
        self.windows = windows
        self.boundary_confidence = boundary_confidence
        self.patrol_start_ts_ms = patrol_start_ts_ms
        self.patrol_end_ts_ms = patrol_end_ts_ms
        self.patrol_id = PATROL_ID

    def zones(self):
        return sorted((w for w in self.windows if w.zone_id != 0), key=lambda w: w.zone_id)


def test_status_abnormal_when_disease_ratio_exceeds_15_percent():
    # 정상=5, 병충해_의심=1 -> ratio 1/6 = 0.1667 > 0.15
    detections = [detection(CropState.NORMAL, 5), detection(CropState.SUSPECTED_DISEASE, 1)]
    seg = FakeSegmentation([zone_window(1, detections)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.zones[0].status == ReportStatus.ABNORMAL


def test_status_caution_when_disease_ratio_between_5_and_15_percent():
    # 정상=13, 병충해_의심=1 -> ratio 1/14 = 0.0714, between 0.05 and 0.15
    detections = [detection(CropState.NORMAL, 13), detection(CropState.SUSPECTED_DISEASE, 1)]
    seg = FakeSegmentation([zone_window(1, detections)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.zones[0].status == ReportStatus.CAUTION


def test_status_caution_from_emergency_stop_alone_even_with_zero_disease():
    # No disease at all (ratio 0.0), but an EMERGENCY_STOP occurred in the zone.
    detections = [detection(CropState.NORMAL, 20)]
    events = [EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=0, type=EventType.EMERGENCY_STOP)]
    seg = FakeSegmentation([zone_window(1, detections, events=events)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.zones[0].status == ReportStatus.CAUTION


def test_status_normal_when_below_all_thresholds():
    detections = [detection(CropState.NORMAL, 20)]
    seg = FakeSegmentation([zone_window(1, detections)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.zones[0].status == ReportStatus.NORMAL


def test_undetermined_flag_exactly_at_threshold_not_set():
    # 판단불가=6 of 20 total -> 0.30 exactly; threshold is "> 0.30", not ">=".
    detections = [detection(CropState.NORMAL, 10), detection(CropState.IMMATURE, 2),
                  detection(CropState.SUSPECTED_DISEASE, 2), detection(CropState.UNDETERMINED, 6, confidence=None)]
    seg = FakeSegmentation([zone_window(1, detections)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    zone = agg.zones[0]
    assert zone.undetermined_rate == pytest.approx(0.30)
    assert "재촬영_필요" not in zone.flags


def test_undetermined_flag_set_just_above_threshold():
    # 판단불가=7 of 20 -> 0.35 > 0.30
    detections = [detection(CropState.NORMAL, 10), detection(CropState.IMMATURE, 2),
                  detection(CropState.SUSPECTED_DISEASE, 1), detection(CropState.UNDETERMINED, 7, confidence=None)]
    seg = FakeSegmentation([zone_window(1, detections)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    zone = agg.zones[0]
    assert zone.undetermined_rate == pytest.approx(0.35)
    assert "재촬영_필요" in zone.flags


def test_zone_with_zero_observations_has_null_undetermined_rate():
    seg = FakeSegmentation([zone_window(1, detections=[])])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    zone = agg.zones[0]
    assert zone.undetermined_rate is None
    assert zone.status == ReportStatus.NORMAL
    assert zone.observations == {}


def test_disease_ratio_denominator_excludes_undetermined():
    # 정상=1, 병충해_의심=1, 판단불가=100 -> disease ratio must be 1/2 = 0.5
    # (이상), NOT 1/102. If undetermined leaked into the denominator, this
    # would compute ~0.0098 and wrongly report 정상.
    detections = [detection(CropState.NORMAL, 1), detection(CropState.SUSPECTED_DISEASE, 1),
                  detection(CropState.UNDETERMINED, 100, confidence=None)]
    seg = FakeSegmentation([zone_window(1, detections)])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.zones[0].status == ReportStatus.ABNORMAL


def test_env_stats_exclude_null_samples():
    window = ZoneWindow(
        zone_id=1, start_ts_ms=0, end_ts_ms=3000,
        telemetry=[telemetry(0, 0, temp_c=20.0), telemetry(1, 1000, temp_c=None), telemetry(2, 2000, temp_c=30.0)],
        analysis=[], events=[],
    )
    seg = FakeSegmentation([window])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    temp = agg.zones[0].env.temp_c
    assert temp.n == 2  # the None sample is excluded, not counted as 0
    assert temp.avg == pytest.approx(25.0)
    assert temp.min == 20.0
    assert temp.max == 30.0


def test_zone_confidence_matches_segmentation_boundary_confidence():
    seg = FakeSegmentation([zone_window(1, [])], boundary_confidence="low")
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.zones[0].confidence == "low"
    assert agg.data_completeness.zone_boundary_confidence == "low"


def test_overall_status_is_worst_zone_status():
    normal_zone = zone_window(1, [detection(CropState.NORMAL, 10)])
    abnormal_zone = zone_window(2, [detection(CropState.NORMAL, 1), detection(CropState.SUSPECTED_DISEASE, 1)])
    seg = FakeSegmentation([normal_zone, abnormal_zone])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.overall_status == ReportStatus.ABNORMAL


def test_overall_status_normal_with_no_zones():
    seg = FakeSegmentation([])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.overall_status == ReportStatus.NORMAL
    assert agg.zones == []


def test_transit_window_excluded_from_zones_but_counted_in_images_analysed():
    transit = ZoneWindow(zone_id=0, start_ts_ms=0, end_ts_ms=100, telemetry=[], analysis=[analysis("t_000", 0, [])], events=[])
    zone1 = zone_window(1, [])
    seg = FakeSegmentation([transit, zone1])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert len(agg.zones) == 1  # transit did not produce a ZoneMetadata
    assert agg.data_completeness.images_analysed == 1  # but is still counted


def test_aggregate_is_deterministic():
    detections = [detection(CropState.NORMAL, 5), detection(CropState.SUSPECTED_DISEASE, 1)]
    seg = FakeSegmentation([zone_window(1, detections)])
    settings = get_settings()
    agg1 = aggregate(seg, udp_received=100, udp_expected=110, settings=settings)
    agg2 = aggregate(seg, udp_received=100, udp_expected=110, settings=settings)
    assert agg1.model_dump_json() == agg2.model_dump_json()


def test_patrol_date_read_from_patrol_id():
    seg = FakeSegmentation([])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.patrol_date == "2026-08-13"


def test_llm_disabled_by_default_before_a5():
    seg = FakeSegmentation([])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert agg.llm.enabled is False
    assert agg.llm.model is None


def test_unknown_vis_state_raises_rather_than_coerces():
    """A2 acceptance criterion, restated at the model layer: by the time any
    row reaches aggregate(), it's already a validated AnalysisResult, so an
    unknown state cannot exist in well-typed input — the enforcement point
    is construction/parsing (models.py's closed CropState enum), which this
    proves directly. See also tests/test_vis_watcher.py's version of this
    at the ingest boundary.
    """
    with pytest.raises(ValidationError):
        Detection.model_validate({"class": "tomato", "state": "병해충", "count": 1, "confidence": 0.9})


def test_zone_id_ordering_in_output_matches_zone_id_not_insertion_order():
    seg = FakeSegmentation([zone_window(3, []), zone_window(1, []), zone_window(2, [])])
    agg = aggregate(seg, udp_received=1, udp_expected=1, settings=get_settings())
    assert [z.zone_id for z in agg.zones] == [1, 2, 3]


def test_full_pipeline_schema_round_trip():
    """segment_patrol -> aggregate -> validates against contracts/schemas/c3-metadata.schema.json."""
    import json
    from datetime import UTC, datetime

    from jsonschema import Draft202012Validator

    events = [
        EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=0, type=EventType.PATROL_START),
        EventMessage(patrol_id=PATROL_ID, event_seq=1, ts_ms=0, type=EventType.ZONE_ENTER, zone_id=1),
        EventMessage(patrol_id=PATROL_ID, event_seq=2, ts_ms=1000, type=EventType.PATROL_END),
    ]
    rows = [telemetry(0, 0), telemetry(1, 500)]
    results = [analysis("z1_000", 0, [detection(CropState.NORMAL, 3)])]

    settings = get_settings()
    seg = segment_patrol(PATROL_ID, rows, events, results, settings)
    agg = aggregate(seg, udp_received=2, udp_expected=2, settings=settings)

    data = agg.model_dump(mode="json")
    data["generated_at"] = datetime.now(UTC).isoformat()
    with open("contracts/schemas/c3-metadata.schema.json") as f:
        schema = json.load(f)
    Draft202012Validator(schema).validate(data)
