"""The A1 phase-done criterion, verbatim:

fake_rover.py can replay a 20-minute synthetic patrol over real UDP to
localhost, including deliberate packet loss, and the computed loss rate
matches the emitter's configured drop rate within 1%.

This uses a real loopback UDP socket between our own fake_rover and our own
udp_listener — see 02-ai-subsystem-spec.md §13, which requires exactly this.
No external service is contacted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ai_report.devtools.fake_rover import choose_drop_indices, generate_patrol_plan, replay
from ai_report.ingest.store import Store
from ai_report.ingest.udp_listener import create_udp_listener
from tests.conftest import free_udp_port

PATROL_ID = "20260813_1430"


async def test_fake_rover_loss_rate_matches_configured_drop_rate(tmp_path: Path):
    store = Store(tmp_path / "sessions.db")
    port = free_udp_port()
    transport, protocol = await create_udp_listener(store, host="127.0.0.1", port=port)

    duration_s = 1200  # 20 simulated minutes
    drop_rate = 0.08
    plan = generate_patrol_plan(PATROL_ID, duration_s=duration_s, num_zones=6, num_estops=2, seed=7)
    drop_indices = choose_drop_indices(len(plan.telemetry), drop_rate, seed=7)

    try:
        # speed=600 compresses the 20-minute timeline into ~2 real seconds.
        # Events ride the UDP fallback here since only the UDP listener is
        # under test, not the HTTP event API.
        stats = await replay(
            plan,
            "127.0.0.1",
            port,
            event_port=0,
            drop_indices=drop_indices,
            speed=600.0,
            udp_fallback=True,
            udp_fallback_resends=3,
        )
        await asyncio.sleep(0.2)  # let the last datagrams land

        assert stats.telemetry_dropped == len(drop_indices)
        assert protocol.malformed_count == 0

        expected = max(store.received_telemetry_seqs(PATROL_ID)) + 1
        assert expected == duration_s  # last packet was never dropped

        measured_loss_rate = store.loss_rate(PATROL_ID)
        assert measured_loss_rate is not None
        assert abs(measured_loss_rate - drop_rate) <= 0.01
    finally:
        transport.close()
        store.close()
