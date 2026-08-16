from __future__ import annotations

import pytest

from ai_report.models import (
    DriveReading,
    DriveState,
    EnvReading,
    EventMessage,
    EventType,
    TelemetryPacket,
)

PATROL_ID = "20260813_1430"


def make_packet(seq: int, patrol_id: str = PATROL_ID) -> TelemetryPacket:
    return TelemetryPacket(
        patrol_id=patrol_id,
        seq=seq,
        ts_ms=seq * 1000,
        type="TELEMETRY",
        zone_id=1,
        env=EnvReading(temp_c=25.0, humid_pct=60.0),
        drive=DriveReading(speed_mps=0.3, steer=0.0, ultra_cm=100, state=DriveState.RUNNING),
    )


def test_insert_telemetry_dedup(store):
    pkt = make_packet(0)
    assert store.insert_telemetry(pkt) is True
    assert store.insert_telemetry(pkt) is False  # duplicate primary key -> no-op
    assert store.received_telemetry_seqs(PATROL_ID) == {0}


def test_out_of_order_insert(store):
    for seq in [3, 1, 2, 0]:
        store.insert_telemetry(make_packet(seq))
    assert store.received_telemetry_seqs(PATROL_ID) == {0, 1, 2, 3}
    assert store.max_telemetry_seq(PATROL_ID) == 3


def test_loss_rate_with_gap(store):
    for seq in [0, 1, 3, 4]:  # seq 2 missing; max seq 4 -> expected 5
        store.insert_telemetry(make_packet(seq))
    assert store.loss_rate(PATROL_ID) == pytest.approx(1 - 4 / 5)


def test_loss_rate_none_when_empty(store):
    assert store.loss_rate(PATROL_ID) is None


def test_loss_rate_zero_when_complete(store):
    for seq in range(10):
        store.insert_telemetry(make_packet(seq))
    assert store.loss_rate(PATROL_ID) == pytest.approx(0.0)


def test_insert_event_idempotent(store):
    evt = EventMessage(patrol_id=PATROL_ID, event_seq=0, ts_ms=0, type=EventType.PATROL_START)
    assert store.insert_event(evt) is True
    assert store.insert_event(evt) is False
    assert len(store.events_for_patrol(PATROL_ID)) == 1


def test_events_for_patrol_ordered(store):
    for seq in [2, 0, 1]:
        store.insert_event(EventMessage(patrol_id=PATROL_ID, event_seq=seq, ts_ms=seq * 1000, type=EventType.ZONE_ENTER, zone_id=seq + 1))
    seqs = [e.event_seq for e in store.events_for_patrol(PATROL_ID)]
    assert seqs == [0, 1, 2]
