from __future__ import annotations

from ai_report.devtools.fake_rover import choose_drop_indices, generate_patrol_plan

PATROL_ID = "20260813_1430"


def test_generate_patrol_plan_shape():
    plan = generate_patrol_plan(PATROL_ID, duration_s=1200, num_zones=6, num_estops=2, seed=1)

    assert len(plan.telemetry) == 1200
    assert [pkt.seq for pkt in plan.telemetry] == list(range(1200))  # monotonic from 0

    zone_enters = [e for e in plan.events if e.type.value == "ZONE_ENTER"]
    estops = [e for e in plan.events if e.type.value == "EMERGENCY_STOP"]
    assert len(zone_enters) == 6
    assert len(estops) == 2
    assert plan.events[0].type.value == "PATROL_START"
    assert plan.events[-1].type.value == "PATROL_END"
    assert [e.event_seq for e in plan.events] == list(range(len(plan.events)))


def test_generate_patrol_plan_deterministic_with_seed():
    plan_a = generate_patrol_plan(PATROL_ID, duration_s=300, seed=42)
    plan_b = generate_patrol_plan(PATROL_ID, duration_s=300, seed=42)
    assert [p.model_dump() for p in plan_a.telemetry] == [p.model_dump() for p in plan_b.telemetry]


def test_choose_drop_indices_matches_configured_rate():
    n = 1200
    drop_rate = 0.08
    indices = choose_drop_indices(n, drop_rate, seed=1)
    actual_rate = len(indices) / n
    assert abs(actual_rate - drop_rate) <= 0.01
    assert (n - 1) not in indices  # last packet is never dropped


def test_choose_drop_indices_zero_rate():
    assert choose_drop_indices(1200, 0.0) == set()
