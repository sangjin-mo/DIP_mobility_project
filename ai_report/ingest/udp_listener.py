"""UDP telemetry ingest (C1.1) plus the UDP fallback path for events (C1.2).

DR's primary channel for events is HTTP (event_api.py). If DR cannot do
HTTP, C1.2 falls back to the same event JSON sent 3x over UDP, deduplicated
by (patrol_id, event_seq). No separate port is specified for that fallback
in the ICD, so both message kinds share UDP_PORT and are dispatched by the
`type` discriminator — TELEMETRY goes to the telemetry table, anything else
recognised in EventType goes through the same idempotent event insert used
by the HTTP path.

Malformed packets are logged and dropped; the listener never crashes on bad
input (error-handling matrix, spec §12).

Called by:
- `cli.py::_serve` — the real `ai-report serve` entry point.
- `devtools/fake_rover.py::replay` — sends the datagrams this module receives.
- `tests/test_udp_listener.py`, `tests/test_a1_acceptance.py` — stand up a
  listener on an ephemeral port and assert on `Store`/protocol counters.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from ai_report.ingest.store import Store
from ai_report.models import EventMessage, EventType, TelemetryPacket

logger = logging.getLogger(__name__)

_EVENT_TYPE_VALUES = {t.value for t in EventType}


class TelemetryUDPProtocol(asyncio.DatagramProtocol):
    """asyncio callback object bound to the UDP socket by `create_udp_listener`.

    asyncio's event loop calls the `connection_made` / `datagram_received` /
    `error_received` methods directly — they are framework callbacks, not
    application code that this codebase calls itself. Per-instance counters
    (`telemetry_received`, `events_received`, `malformed_count`) exist so
    tests and the smoke-tested `cli.py serve` can observe what happened
    without querying the database.
    """

    def __init__(self, store: Store) -> None:
        """Store a reference to the shared `Store` and zero the counters.

        Called once by the `lambda: TelemetryUDPProtocol(store)` factory
        passed to `loop.create_datagram_endpoint` in `create_udp_listener`.
        """
        self._store = store
        self.transport: asyncio.DatagramTransport | None = None
        self.telemetry_received = 0
        self.events_received = 0
        self.malformed_count = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """asyncio callback fired once the UDP socket is bound and ready.

        Keeps the transport so it could be used to send from this protocol
        later (not currently needed, but is the standard asyncio pattern).
        """
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """asyncio callback fired once per incoming UDP datagram. Delegates to `_handle`."""
        self._handle(data, addr)

    def error_received(self, exc: Exception) -> None:
        """asyncio callback fired on OS-level socket errors (not per-packet parse errors)."""
        logger.warning("UDP error: %s", exc)

    def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        """Parse one datagram's JSON, dispatch by its `type` field, and store it.

        Dispatch logic:
        - `type == "TELEMETRY"` -> validate as `TelemetryPacket`, call
          `Store.insert_telemetry`.
        - `type` is one of the five `EventType` values -> validate as
          `EventMessage`, call `Store.insert_event` (this is the C1.2 UDP
          fallback path; the HTTP primary path is `event_api.py::post_event`).
        - anything else, or a `ValidationError` from either branch -> log a
          warning and increment `malformed_count`. The exception is caught
          here specifically so one bad packet never crashes the listener.

        Called by `datagram_received` for every packet; calls `_parse_json`
        first to get a JSON object (or bail out early on non-JSON input).
        """
        raw = self._parse_json(data, addr)
        if raw is None:
            return

        msg_type = raw.get("type")
        try:
            if msg_type == "TELEMETRY":
                pkt = TelemetryPacket.model_validate(raw)
                self._store.insert_telemetry(pkt)
                self.telemetry_received += 1
            elif msg_type in _EVENT_TYPE_VALUES:
                evt = EventMessage.model_validate(raw)
                self._store.insert_event(evt)
                self.events_received += 1
            else:
                logger.warning("unknown UDP message type %r from %s", msg_type, addr)
                self.malformed_count += 1
        except ValidationError as exc:
            logger.warning("malformed UDP packet from %s: %s", addr, exc)
            self.malformed_count += 1

    def _parse_json(self, data: bytes, addr: tuple[str, int]) -> dict[str, Any] | None:
        """Decode raw bytes to a JSON object, or return None and log+count a rejection.

        Three ways a datagram can fail here: not valid UTF-8, not valid
        JSON, or valid JSON that isn't an object (e.g. a bare number or
        array). Any of these increments `malformed_count` the same way a
        `ValidationError` in `_handle` does. Called only by `_handle`.
        """
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("non-UTF-8 UDP packet from %s", addr)
            self.malformed_count += 1
            return None
        try:
            raw = json.loads(decoded)
        except json.JSONDecodeError:
            logger.warning("non-JSON UDP packet from %s", addr)
            self.malformed_count += 1
            return None
        if not isinstance(raw, dict):
            logger.warning("UDP packet from %s is not a JSON object", addr)
            self.malformed_count += 1
            return None
        return raw


async def create_udp_listener(
    store: Store, host: str = "0.0.0.0", port: int = 9100
) -> tuple[asyncio.DatagramTransport, TelemetryUDPProtocol]:
    """Bind a UDP socket on `host:port` and start routing datagrams into `store`.

    Thin wrapper around `loop.create_datagram_endpoint` that fixes the
    protocol factory to `TelemetryUDPProtocol(store)`. Returns both the
    transport (call `.close()` on it to stop listening) and the protocol
    instance (read its counters for diagnostics/tests).

    Called by `cli.py::_serve` (with the real configured host/port) and
    directly by `tests/test_udp_listener.py` / `tests/test_a1_acceptance.py`
    (with an ephemeral port from `tests/conftest.py::free_udp_port`).
    """
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: TelemetryUDPProtocol(store), local_addr=(host, port)
    )
    return transport, protocol
