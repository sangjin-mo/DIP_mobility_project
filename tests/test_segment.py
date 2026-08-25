from __future__ import annotations

from ai_report.config import get_settings
from ai_report.models import (
    AnalysisResult,
    Detection,
    DriveReading,
    DriveState,
    EnvReading,
    EventMessage,
    EventType,
    TelemetryPacket,
)
from ai_report.pipeline.segment import (
    _boundaries_from_distance,
    dominant_crop_class,
    segment_by_crop_type,
    segment_patrol,
)

PATROL_ID = "20260813_1430"


def detection(class_: str, count: int, confidence: float | None = 0.9) -> Detection:
    return Detection.model_validate({"class": class_, "state": "정상", "count": count, "confidence": confidence})


def analysis(image_id: str, ts_ms: int, detections: list[Detection]) -> AnalysisResult:
    return AnalysisResult(
        image_id=image_id,
        patrol_id=PATROL_ID,
        captured_at_ms=ts_ms,
        image_path=f"images/{PATROL_ID}/{image_id}.jpg",
        image_quality=0.8,
        detections=detections,
    )


def telemetry(seq: int, ts_ms: int, state: DriveState = DriveState.RUNNING, speed: float = 0.3) -> TelemetryPacket:
    return TelemetryPacket(
        patrol_id=PATROL_ID,
        seq=seq,
        ts_ms=ts_ms,
        type="TELEMETRY",
        zone_id=None,
        env=EnvReading(temp_c=25.0, humid_pct=60.0),
        drive=DriveReading(speed_mps=speed, steer=0.0, ultra_cm=100, state=state),
    )


def event(event_seq: int, ts_ms: int, event_type: EventType, zone_id: int | None = None, detail: dict | None = None) -> EventMessage:
    return EventMessage(
        patrol_id=PATROL_ID, event_seq=event_seq, ts_ms=ts_ms, type=event_type, zone_id=zone_id, detail=detail or {}
    )


def test_emergency_stop_does_not_shift_boundary():
    """The A2 acceptance test the build plan says to write first."""
    events = [
        event(0, 0, EventType.PATROL_START),
        event(1, 1000, EventType.ZONE_ENTER, zone_id=1),
        event(2, 3000, EventType.EMERGENCY_STOP, detail={"ultra_cm": 8}),  # mid zone 1
        event(3, 5000, EventType.ZONE_ENTER, zone_id=2),
        event(4, 9000, EventType.PATROL_END, detail={"reason": "completed"}),
    ]
    rows = [telemetry(i, i * 1000) for i in range(10)]  # ts_ms 0..9000

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    assert seg.boundary_confidence == "high"
    zone1 = next(w for w in seg.windows if w.zone_id == 1)
    zone2 = next(w for w in seg.windows if w.zone_id == 2)
    assert (zone1.start_ts_ms, zone1.end_ts_ms) == (1000, 5000)
    assert (zone2.start_ts_ms, zone2.end_ts_ms) == (5000, 9000)
    # the EMERGENCY_STOP landed inside zone 1, but did not move zone 2's start
    assert any(e.type == EventType.EMERGENCY_STOP for e in zone1.events)


def test_transit_segment_before_first_zone_enter():
    events = [
        event(0, 0, EventType.PATROL_START),
        event(1, 2000, EventType.ZONE_ENTER, zone_id=1),
        event(2, 4000, EventType.PATROL_END, detail={"reason": "completed"}),
    ]
    rows = [telemetry(0, 0), telemetry(1, 1000), telemetry(2, 2500), telemetry(3, 3500)]

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    transit = next(w for w in seg.windows if w.zone_id == 0)
    assert (transit.start_ts_ms, transit.end_ts_ms) == (0, 2000)
    assert {t.seq for t in transit.telemetry} == {0, 1}
    assert 0 not in {w.zone_id for w in seg.zones()}  # zones() excludes transit


def test_no_transit_segment_when_first_zone_enter_is_at_patrol_start():
    events = [
        event(0, 0, EventType.PATROL_START),
        event(1, 0, EventType.ZONE_ENTER, zone_id=1),
        event(2, 2000, EventType.PATROL_END),
    ]
    rows = [telemetry(0, 0), telemetry(1, 1000)]

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    assert 0 not in {w.zone_id for w in seg.windows}


def test_last_zone_end_is_inclusive_of_patrol_end():
    events = [
        event(0, 0, EventType.PATROL_START),
        event(1, 0, EventType.ZONE_ENTER, zone_id=1),
        event(2, 5000, EventType.PATROL_END),
    ]
    rows = [telemetry(0, 0), telemetry(1, 5000)]  # last sample exactly at PATROL_END

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    zone1 = next(w for w in seg.windows if w.zone_id == 1)
    assert {t.seq for t in zone1.telemetry} == {0, 1}  # seq 1 at ts=5000 not dropped


def test_fallback_segmentation_when_no_zone_enter_events():
    events = [event(0, 0, EventType.PATROL_START), event(1, 1200, EventType.PATROL_END)]
    rows = [telemetry(i, i * 100, speed=1.0) for i in range(13)]  # ts_ms 0..1200, 1 m/s

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    assert seg.boundary_confidence == "low"
    assert len(seg.zones()) == get_settings().ROUTE_ZONE_COUNT
    # every telemetry row still lands in exactly one window
    assert sum(len(w.telemetry) for w in seg.windows) == len(rows)


def test_fallback_excludes_stopped_and_emergency_intervals_from_distance():
    """Direct unit test of the distance-integration mechanic (spec §5's fallback
    path): a STOPPED interval must contribute zero distance, not `speed_mps * dt`.

    Two zones, 15m each (ROUTE_TOTAL_DISTANCE_M=30). Timeline: 10s RUNNING at
    1 m/s (+10m), 10s STOPPED at 1 m/s (+0m if excluded, +10m if not), 10s
    RUNNING at 1 m/s (+10m). Correctly excluding the STOPPED interval means
    cumulative distance only reaches 20m (crossing the 15m zone boundary) at
    the very last sample, t=30000 — an implementation that wrongly counted
    the STOPPED interval would instead read 20m already at t=20000, moving
    the boundary 10 seconds earlier. This is exactly the kind of bug hard
    rule 4 exists to catch, just in the fallback path instead of the primary one.
    """
    settings = get_settings().model_copy(update={"ROUTE_ZONE_COUNT": 2, "ROUTE_TOTAL_DISTANCE_M": 30.0})
    rows = [
        telemetry(0, 0, state=DriveState.RUNNING, speed=1.0),
        telemetry(1, 10_000, state=DriveState.STOPPED, speed=1.0),
        telemetry(2, 20_000, state=DriveState.RUNNING, speed=1.0),
        telemetry(3, 30_000, state=DriveState.RUNNING, speed=1.0),
    ]

    boundaries = _boundaries_from_distance(rows, patrol_end_ts_ms=30_000, settings=settings)

    zone1 = next(b for b in boundaries if b[0] == 1)
    zone2 = next(b for b in boundaries if b[0] == 2)
    assert zone1 == (1, 0, 30_000)  # would be (1, 0, 20_000) if STOPPED time weren't excluded
    assert zone2 == (2, 30_000, 30_000)


def test_missing_patrol_start_and_end_fall_back_to_row_timestamps():
    events = [event(0, 100, EventType.ZONE_ENTER, zone_id=1)]  # no PATROL_START/PATROL_END
    rows = [telemetry(0, 100), telemetry(1, 900)]

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    assert seg.patrol_start_ts_ms == 100  # earliest telemetry row
    assert seg.patrol_end_ts_ms == 900  # latest telemetry row
    zone1 = next(w for w in seg.windows if w.zone_id == 1)
    assert zone1.end_ts_ms == 900


def test_out_of_order_input_is_sorted_before_segmenting():
    events = [
        event(2, 4000, EventType.PATROL_END),
        event(0, 0, EventType.PATROL_START),
        event(1, 1000, EventType.ZONE_ENTER, zone_id=1),
    ]
    rows = [telemetry(2, 3000), telemetry(0, 500), telemetry(1, 1500)]

    seg = segment_patrol(PATROL_ID, rows, events, [], get_settings())

    zone1 = next(w for w in seg.windows if w.zone_id == 1)
    assert {t.seq for t in zone1.telemetry} == {1, 2}


def test_empty_input_produces_no_windows():
    seg = segment_patrol(PATROL_ID, [], [], [], get_settings())
    assert seg.windows == []
    assert seg.zones() == []


# --- ADR-0009: segment_by_crop_type ----------------------------------------


def test_dominant_crop_class_picks_highest_count():
    result = analysis("z1", 0, [detection("tomato", 2), detection("chili_pepper", 5)])
    assert dominant_crop_class(result) == "chili_pepper"


def test_dominant_crop_class_breaks_ties_alphabetically():
    result = analysis("z1", 0, [detection("tomato", 3), detection("chili_pepper", 3)])
    assert dominant_crop_class(result) == "chili_pepper"  # "chili_pepper" < "tomato"


def test_dominant_crop_class_none_when_no_detections():
    assert dominant_crop_class(analysis("z1", 0, [])) is None


def test_segment_by_crop_type_groups_images_by_dominant_class():
    events = [event(0, 0, EventType.PATROL_START), event(1, 5000, EventType.PATROL_END)]
    images = [
        analysis("a", 1000, [detection("tomato", 4)]),
        analysis("b", 2000, [detection("tomato", 2)]),
        analysis("c", 3000, [detection("chili_pepper", 1)]),
    ]

    seg = segment_by_crop_type(PATROL_ID, events, images)

    assert seg.boundary_confidence == "low"
    assert seg.patrol_start_ts_ms == 0
    assert seg.patrol_end_ts_ms == 5000
    zones = seg.zones()
    assert [z.zone_id for z in zones] == [1, 2]  # alphabetical: chili_pepper, tomato
    by_zone = {z.zone_id: {a.image_id for a in z.analysis} for z in zones}
    assert by_zone[1] == {"c"}  # chili_pepper
    assert by_zone[2] == {"a", "b"}  # tomato


def test_segment_by_crop_type_puts_no_detection_images_in_zone_zero():
    images = [analysis("empty", 1000, [])]

    seg = segment_by_crop_type(PATROL_ID, [], images)

    assert seg.zones() == []  # zone_id=0 excluded from zone reporting
    assert len(seg.windows) == 1
    assert seg.windows[0].zone_id == 0
    assert {a.image_id for a in seg.windows[0].analysis} == {"empty"}


def test_segment_by_crop_type_falls_back_to_image_timestamps_without_events():
    images = [analysis("a", 1000, [detection("tomato", 1)]), analysis("b", 4000, [detection("tomato", 1)])]

    seg = segment_by_crop_type(PATROL_ID, [], images)

    assert seg.patrol_start_ts_ms == 1000
    assert seg.patrol_end_ts_ms == 4000


def test_segment_by_crop_type_empty_input_produces_no_windows():
    seg = segment_by_crop_type(PATROL_ID, [], [])
    assert seg.windows == []
    assert seg.zones() == []
    assert seg.patrol_start_ts_ms == 0
    assert seg.patrol_end_ts_ms == 0


def test_segment_by_crop_type_reports_drive_obstructions_once():
    """`obstruction_counts()` was unconditionally empty on this path.

    Crop-type windows carried no events at all, so the report's 통로 장애 요인
    section and `Payload.obstructions` could never be populated on the only
    segmentation path production uses — the LLM was never told about a single
    emergency stop or line loss. Every window spans the whole patrol, so the
    events attach to one zone rather than all of them; attaching to all would
    multiply each event by the number of crop types.
    """
    events = [
        event(0, 0, EventType.PATROL_START),
        event(1, 1500, EventType.EMERGENCY_STOP),
        event(2, 2500, EventType.LINE_LOST),
        event(3, 5000, EventType.PATROL_END),
    ]
    images = [
        analysis("a", 1000, [detection("tomato", 4)]),
        analysis("c", 3000, [detection("chili_pepper", 1)]),
    ]

    seg = segment_by_crop_type(PATROL_ID, events, images)
    counts = seg.obstruction_counts()

    assert counts == {1: {"EMERGENCY_STOP": 1, "LINE_LOST": 1}}
    total = sum(n for per_zone in counts.values() for n in per_zone.values())
    assert total == 2, "each drive event must be counted once, not once per crop type"


def test_segment_by_crop_type_reports_nothing_when_there_were_no_obstructions():
    events = [event(0, 0, EventType.PATROL_START), event(1, 5000, EventType.PATROL_END)]
    images = [analysis("a", 1000, [detection("tomato", 4)])]

    assert segment_by_crop_type(PATROL_ID, events, images).obstruction_counts() == {}
