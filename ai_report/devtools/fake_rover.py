"""DR emitter stand-in. Every later phase is developed against this, not
real hardware — see GUIDELINES.md and 03-build-plan.md, A1.

Generates a synthetic patrol (telemetry at 1 Hz, ZONE_ENTER/EMERGENCY_STOP/
PATROL_START/PATROL_END events) and replays it over real UDP (telemetry)
and, by default, real HTTP (events — C1.2's primary channel; pass
--udp-fallback to exercise the 3x-UDP fallback instead).

`generate_patrol_plan` and `choose_drop_indices` are pure and network-free,
so the timeline and the drop pattern can be asserted on directly in tests.
`replay` is the only part that touches a socket.

Call flow for `python -m ai_report.devtools.fake_rover` (`main`):
  main
   |- build_arg_parser        (parse CLI flags)
   |- get_settings             (config.py, for default ports)
   |- generate_patrol_plan     (build the timeline; pure)
   |- choose_drop_indices      (decide which telemetry indices to skip; pure)
   `- replay                   (actually send everything over the network)
       |- _post_event_http     (HTTP path, one event at a time)
       `- socket.sendto        (UDP path, telemetry always + events when --udp-fallback)

The receiving side of that network traffic is `ingest/udp_listener.py`
(telemetry, and events when `--udp-fallback`) and `ingest/event_api.py`
(events, by default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_report.config import get_settings
from ai_report.models import (
    DriveReading,
    DriveState,
    EnvReading,
    EventMessage,
    EventType,
    TelemetryPacket,
)

logger = logging.getLogger(__name__)


@dataclass
class PatrolPlan:
    """The full synthetic timeline produced by `generate_patrol_plan`: every
    telemetry packet and every event, not yet sent anywhere. Consumed by `replay`.
    """

    patrol_id: str
    telemetry: list[TelemetryPacket] = field(default_factory=list)
    events: list[EventMessage] = field(default_factory=list)


@dataclass
class ReplayStats:
    """Counters returned by `replay`, summarising what was actually put on the wire."""

    telemetry_sent: int = 0
    telemetry_dropped: int = 0
    events_sent: int = 0


def generate_patrol_plan(
    patrol_id: str,
    duration_s: int = 1200,
    hz: float = 1.0,
    num_zones: int = 6,
    num_estops: int = 2,
    seed: int | None = 0,
) -> PatrolPlan:
    """Build a full synthetic patrol timeline. Pure — no I/O, no sleeping.

    Deterministic for a fixed `seed`: same inputs always produce
    byte-identical output, which is what lets tests assert on exact shape
    (`tests/test_fake_rover.py::test_generate_patrol_plan_deterministic_with_seed`).

    How it builds the timeline:
    1. Zone boundaries: an initial 5%-of-duration "transit" period (zone_id
       0, before the first ZONE_ENTER — matches spec §5's transit-segment
       rule), then `num_zones` equal-length zones.
    2. Events, in `event_seq` order: PATROL_START at t=0, one ZONE_ENTER per
       zone boundary, one EMERGENCY_STOP at the midpoint of each of the
       first `num_estops` zones, PATROL_END at `duration_s`.
    3. Telemetry, one packet per `1/hz` seconds: `zone_for_ts` assigns each
       packet's `zone_id` from the same boundaries used for the events (so
       the fake data is self-consistent), `in_estop_window` flips
       `drive.state` to EMERGENCY for a few seconds around each configured
       stop, and `env` readings get a small deterministic sinusoidal drift
       plus a rare (1%) simulated sensor dropout to `null`.

    Called by `main` (CLI use) and directly by
    `tests/test_fake_rover.py` / `tests/test_a1_acceptance.py`.
    """
    rng = random.Random(seed)
    n_packets = int(duration_s * hz)

    transit_s = duration_s * 0.05
    zone_span_s = (duration_s - transit_s) / num_zones
    zone_enter_ts_ms = [int((transit_s + i * zone_span_s) * 1000) for i in range(num_zones)]

    estop_ts_ms = [
        int(zone_enter_ts_ms[i] + zone_span_s * 1000 * 0.5) for i in range(min(num_estops, num_zones))
    ]
    estop_window_s = 3

    events: list[EventMessage] = []
    seq = 0
    events.append(
        EventMessage(
            patrol_id=patrol_id,
            event_seq=seq,
            ts_ms=0,
            type=EventType.PATROL_START,
            detail={"route_id": "greenhouse-a"},
        )
    )
    seq += 1
    for zone_id, ts_ms in enumerate(zone_enter_ts_ms, start=1):
        events.append(
            EventMessage(
                patrol_id=patrol_id, event_seq=seq, ts_ms=ts_ms, type=EventType.ZONE_ENTER, zone_id=zone_id
            )
        )
        seq += 1
    for ts_ms in estop_ts_ms:
        events.append(
            EventMessage(
                patrol_id=patrol_id,
                event_seq=seq,
                ts_ms=ts_ms,
                type=EventType.EMERGENCY_STOP,
                detail={"ultra_cm": 8},
            )
        )
        seq += 1
    events.append(
        EventMessage(
            patrol_id=patrol_id,
            event_seq=seq,
            ts_ms=duration_s * 1000,
            type=EventType.PATROL_END,
            detail={"reason": "completed"},
        )
    )

    def zone_for_ts(ts_ms: int) -> int:
        """Which zone (0 = transit) contains timestamp `ts_ms`, per the boundaries above.

        Local closure — only used inside the telemetry-generation loop
        below, mirroring the same event-based-segmentation logic that
        `pipeline/segment.py` will implement for real in A2 (spec §5).
        """
        zone_id = 0
        for i, boundary in enumerate(zone_enter_ts_ms, start=1):
            if ts_ms >= boundary:
                zone_id = i
            else:
                break
        return zone_id

    def in_estop_window(ts_ms: int) -> bool:
        """Whether `ts_ms` falls within `estop_window_s` of any configured EMERGENCY_STOP.

        Local closure used only in the telemetry-generation loop below, to
        decide which packets get `drive.state = EMERGENCY`.
        """
        return any(abs(ts_ms - e) <= estop_window_s * 1000 for e in estop_ts_ms)

    telemetry: list[TelemetryPacket] = []
    for i in range(n_packets):
        ts_ms = int(i * (1000 / hz))
        emergency = in_estop_window(ts_ms)

        temp_c: float | None = round(27.0 + math.sin(i / 180) * 2 + rng.uniform(-0.3, 0.3), 2)
        humid_pct: float | None = round(65.0 + math.cos(i / 240) * 5 + rng.uniform(-0.5, 0.5), 2)
        if rng.random() < 0.01:  # occasional sensor read failure
            temp_c = None
            humid_pct = None

        telemetry.append(
            TelemetryPacket(
                patrol_id=patrol_id,
                seq=i,
                ts_ms=ts_ms,
                type="TELEMETRY",
                zone_id=zone_for_ts(ts_ms),
                env=EnvReading(temp_c=temp_c, humid_pct=humid_pct),
                drive=DriveReading(
                    speed_mps=0.0 if emergency else round(0.25 + rng.uniform(-0.03, 0.05), 3),
                    steer=round(rng.uniform(-0.2, 0.2), 3),
                    ultra_cm=8 if emergency else round(120 + rng.uniform(-20, 40)),
                    state=DriveState.EMERGENCY if emergency else DriveState.RUNNING,
                ),
            )
        )

    return PatrolPlan(patrol_id=patrol_id, telemetry=telemetry, events=events)


def choose_drop_indices(n_packets: int, drop_rate: float, seed: int | None = 0) -> set[int]:
    """Deterministically pick which telemetry indices to withhold.

    Never drops the last packet, so the receiver's `max(seq)+1` expected
    count always equals `n_packets` and the measured loss rate can be
    compared exactly against `drop_rate` rather than only statistically.

    Implementation: `round(drop_rate * n_packets)` indices are sampled
    without replacement (`random.Random(seed).sample`) from
    `range(n_packets - 1)` — excluding the final index on purpose. This
    makes the *actual* drop fraction match the *configured* `drop_rate` to
    within half a packet out of `n_packets` (negligible for the 1200-packet
    default), rather than merely converging on it statistically the way
    independent per-packet coin-flips would.

    Called by `main` (CLI use) and directly by
    `tests/test_fake_rover.py` / `tests/test_a1_acceptance.py`. The
    resulting set is passed into `replay` as `drop_indices`.
    """
    if drop_rate <= 0 or n_packets <= 1:
        return set()
    rng = random.Random(seed)
    droppable = list(range(n_packets - 1))
    drop_count = min(round(drop_rate * n_packets), len(droppable))
    return set(rng.sample(droppable, drop_count))


def _post_event_http(host: str, port: int, event: EventMessage) -> None:
    """Synchronously POST one event as JSON to `http://host:port/api/events`.

    Uses stdlib `urllib.request` (blocking) rather than an async HTTP
    client — event volume per patrol is tiny (~10), so `replay` calls this
    via `asyncio.to_thread` to avoid stalling the event loop for the
    duration of the request. A failed POST (e.g. connection refused) is
    logged and swallowed, matching a real rover's "best effort, keep
    driving" posture rather than aborting the whole replay.

    Called by `replay`, only when `udp_fallback=False` (the default).
    """
    body = json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/api/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.URLError as exc:
        logger.warning("event POST failed for event_seq=%s: %s", event.event_seq, exc)


async def replay(
    plan: PatrolPlan,
    host: str,
    udp_port: int,
    event_port: int,
    drop_indices: set[int],
    speed: float = 60.0,
    udp_fallback: bool = False,
    udp_fallback_resends: int = 3,
) -> ReplayStats:
    """Send the plan over real sockets, paced by `ts_ms` compressed by `speed`.

    Merges `plan.events` and `plan.telemetry` into one timeline sorted by
    `ts_ms` (events break ties before telemetry at the same timestamp), then
    walks it in order: before sending each item, `asyncio.sleep`s just long
    enough that wall-clock time between sends matches simulated time
    between them divided by `speed` (so `speed=60` replays a 20-minute
    patrol in 20 real seconds).

    Per item:
    - **event**: if `udp_fallback`, JSON-encode and `sock.sendto` it
      `udp_fallback_resends` times (matching ICD §C1.2's "sent 3 times"
      fallback); otherwise `await asyncio.to_thread(_post_event_http, ...)`.
    - **telemetry** (index `idx`): if `idx in drop_indices`, count it as
      dropped and skip sending entirely (this is where deliberate packet
      loss actually happens); otherwise JSON-encode and `sock.sendto` it to
      `udp_port`.

    Returns `ReplayStats` with final send/drop counts. Called by `main`
    (CLI use) and directly by `tests/test_a1_acceptance.py`, which points it
    at a listener bound to an ephemeral port instead of the real one.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stats = ReplayStats()

    timeline: list[tuple[int, int, object]] = [(e.ts_ms, 0, e) for e in plan.events] + [
        (t.ts_ms, 1, (i, t)) for i, t in enumerate(plan.telemetry)
    ]
    timeline.sort(key=lambda item: (item[0], item[1]))

    last_ts_ms = 0
    try:
        for ts_ms, kind, payload in timeline:
            delay_s = max(0.0, (ts_ms - last_ts_ms) / 1000.0 / speed)
            if delay_s:
                await asyncio.sleep(delay_s)
            last_ts_ms = ts_ms

            if kind == 0:
                event: EventMessage = payload  # type: ignore[assignment]
                if udp_fallback:
                    body = json.dumps(
                        event.model_dump(mode="json", by_alias=True), ensure_ascii=False
                    ).encode("utf-8")
                    for _ in range(udp_fallback_resends):
                        sock.sendto(body, (host, udp_port))
                else:
                    await asyncio.to_thread(_post_event_http, host, event_port, event)
                stats.events_sent += 1
            else:
                idx, pkt = payload  # type: ignore[misc]
                if idx in drop_indices:
                    stats.telemetry_dropped += 1
                    continue
                body = json.dumps(pkt.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
                sock.sendto(body, (host, udp_port))
                stats.telemetry_sent += 1
    finally:
        sock.close()

    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    """Define the `fake_rover` command-line interface. Called only by `main`."""
    parser = argparse.ArgumentParser(prog="fake_rover", description="Replay a synthetic DR patrol")
    parser.add_argument("--patrol-id", default=None, help="defaults to now, YYYYMMDD_HHMM")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=None)
    parser.add_argument("--event-port", type=int, default=None)
    parser.add_argument("--duration-s", type=int, default=1200, help="20 minutes nominal")
    parser.add_argument("--zones", type=int, default=6)
    parser.add_argument("--estops", type=int, default=2)
    parser.add_argument("--drop-rate", type=float, default=0.0)
    parser.add_argument("--speed", type=float, default=60.0, help="time compression multiplier")
    parser.add_argument("--udp-fallback", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, build a plan, pick drops, replay it, report stats.

    Ties together every other function in this module — see the module
    docstring's call-flow diagram. Called by
    `python -m ai_report.devtools.fake_rover ...` (the `if __name__` guard
    below) and, indirectly, by anyone running the devtool from a shell.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_arg_parser().parse_args(argv)
    settings = get_settings()

    patrol_id = args.patrol_id or datetime.now(UTC).strftime("%Y%m%d_%H%M")
    udp_port = args.udp_port or settings.UDP_PORT
    event_port = args.event_port or settings.EVENT_PORT

    plan = generate_patrol_plan(
        patrol_id,
        duration_s=args.duration_s,
        num_zones=args.zones,
        num_estops=args.estops,
        seed=args.seed,
    )
    drop_indices = choose_drop_indices(len(plan.telemetry), args.drop_rate, seed=args.seed)

    stats = asyncio.run(
        replay(
            plan,
            args.host,
            udp_port,
            event_port,
            drop_indices,
            speed=args.speed,
            udp_fallback=args.udp_fallback,
            udp_fallback_resends=settings.EVENT_UDP_FALLBACK_RESENDS,
        )
    )
    logger.info(
        "patrol_id=%s telemetry_sent=%d telemetry_dropped=%d events_sent=%d configured_drop_rate=%.4f",
        patrol_id,
        stats.telemetry_sent,
        stats.telemetry_dropped,
        stats.events_sent,
        args.drop_rate,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
