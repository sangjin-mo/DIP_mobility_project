"""Read the latest live state from the SQLite database owned by ``ai_report``.

The first dashboard version deliberately does not create another UDP listener.
The existing ``TelemetryUDPProtocol`` remains the sole writer; this adapter
opens short-lived, read-only SQLite connections and turns rows back into the
existing Pydantic boundary models.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ai_report.models import EventMessage, TelemetryPacket


class LiveStateService:
    def __init__(self, sqlite_path: Path, stale_after_s: float = 3.0) -> None:
        self._sqlite_path = Path(sqlite_path)
        self._stale_after_ms = int(stale_after_s * 1000)

    def snapshot(self) -> dict:
        now_ms = int(time.time() * 1000)
        if not self._sqlite_path.is_file():
            return self._empty_snapshot(now_ms, "telemetry database does not exist yet")

        try:
            with self._connect_read_only() as connection:
                telemetry = self._latest_telemetry(connection)
                event = self._latest_event(connection)
        except sqlite3.Error as exc:
            return self._empty_snapshot(now_ms, f"database read failed: {exc}")

        telemetry_data = telemetry.model_dump(mode="json") if telemetry else None
        event_data = event.model_dump(mode="json") if event else None
        connected = bool(
            telemetry and 0 <= now_ms - telemetry.ts_ms <= self._stale_after_ms
        )
        return {
            "connected": connected,
            "server_time_ms": now_ms,
            "telemetry": telemetry_data,
            "latest_event": event_data,
            "error": None,
        }

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"{self._sqlite_path.resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=1.0)

    @staticmethod
    def _latest_telemetry(connection: sqlite3.Connection) -> TelemetryPacket | None:
        row = connection.execute(
            """
            SELECT patrol_id, seq, ts_ms, zone_id, temp_c, humid_pct,
                   speed_mps, steer, ultra_cm, state
            FROM telemetry
            ORDER BY ts_ms DESC, seq DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return TelemetryPacket(
            patrol_id=row[0],
            seq=row[1],
            ts_ms=row[2],
            type="TELEMETRY",
            zone_id=row[3],
            env={"temp_c": row[4], "humid_pct": row[5]},
            drive={
                "speed_mps": row[6],
                "steer": row[7],
                "ultra_cm": row[8],
                "state": row[9],
            },
        )

    @staticmethod
    def _latest_event(connection: sqlite3.Connection) -> EventMessage | None:
        row = connection.execute(
            """
            SELECT patrol_id, event_seq, ts_ms, type, zone_id, detail
            FROM events
            ORDER BY ts_ms DESC, event_seq DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return EventMessage(
            patrol_id=row[0],
            event_seq=row[1],
            ts_ms=row[2],
            type=row[3],
            zone_id=row[4],
            detail=json.loads(row[5]) if row[5] else {},
        )

    @staticmethod
    def _empty_snapshot(now_ms: int, error: str) -> dict:
        return {
            "connected": False,
            "server_time_ms": now_ms,
            "telemetry": None,
            "latest_event": None,
            "error": error,
        }
