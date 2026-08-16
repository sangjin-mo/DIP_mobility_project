from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from ai_report.ingest.store import Store
from ai_report.ingest.udp_listener import create_udp_listener
from tests.conftest import free_udp_port

PATROL_ID = "20260813_1430"


def telemetry_dict(seq: int, patrol_id: str = PATROL_ID) -> dict:
    return {
        "patrol_id": patrol_id,
        "seq": seq,
        "ts_ms": seq * 1000,
        "type": "TELEMETRY",
        "zone_id": 1,
        "env": {"temp_c": 25.0, "humid_pct": 60.0},
        "drive": {"speed_mps": 0.3, "steer": 0.0, "ultra_cm": 100, "state": "RUNNING"},
    }


def _send(port: int, obj: dict) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json.dumps(obj, ensure_ascii=False).encode("utf-8"), ("127.0.0.1", port))
    sock.close()


def _send_bytes(port: int, data: bytes) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(data, ("127.0.0.1", port))
    sock.close()


@pytest.fixture
async def listener(tmp_path: Path):
    store = Store(tmp_path / "sessions.db")
    port = free_udp_port()
    transport, protocol = await create_udp_listener(store, host="127.0.0.1", port=port)
    yield store, protocol, port
    transport.close()
    store.close()


async def test_udp_ingest(listener):
    store, _protocol, port = listener
    _send(port, telemetry_dict(0))
    await asyncio.sleep(0.05)
    assert store.received_telemetry_seqs(PATROL_ID) == {0}


async def test_dedup(listener):
    store, protocol, port = listener
    _send(port, telemetry_dict(0))
    _send(port, telemetry_dict(0))
    await asyncio.sleep(0.05)
    assert store.received_telemetry_seqs(PATROL_ID) == {0}
    assert protocol.telemetry_received == 2  # both parsed; store silently dedups


async def test_out_of_order(listener):
    store, _protocol, port = listener
    for seq in [3, 1, 2, 0]:
        _send(port, telemetry_dict(seq))
    await asyncio.sleep(0.05)
    assert store.received_telemetry_seqs(PATROL_ID) == {0, 1, 2, 3}


async def test_malformed_packet_logged_and_dropped_without_crash(listener):
    store, protocol, port = listener
    _send_bytes(port, b"not json at all")
    _send_bytes(port, json.dumps({"type": "TELEMETRY"}).encode("utf-8"))  # missing required fields
    await asyncio.sleep(0.05)
    assert protocol.malformed_count == 2

    # listener must still be alive and functional afterward
    _send(port, telemetry_dict(0))
    await asyncio.sleep(0.05)
    assert store.received_telemetry_seqs(PATROL_ID) == {0}


async def test_event_via_udp_fallback(listener):
    store, _protocol, port = listener
    evt = {
        "patrol_id": PATROL_ID,
        "event_seq": 0,
        "ts_ms": 0,
        "type": "PATROL_START",
        "detail": {"route_id": "greenhouse-a"},
    }
    _send(port, evt)
    _send(port, evt)  # resent per C1.2 fallback; must be idempotent
    await asyncio.sleep(0.05)
    events = store.events_for_patrol(PATROL_ID)
    assert len(events) == 1
    assert events[0].type.value == "PATROL_START"
