"""SQLite persistence for telemetry, events, and VIS analysis (spec §4).

The composite PRIMARY KEY on (patrol_id, seq) / (patrol_id, event_seq) /
(patrol_id, image_id) gives UDP dedup and event idempotency for free: a
repeated row is an INSERT OR IGNORE no-op.

Called by:
- `cli.py::_serve` — constructs the one `Store` shared by the UDP listener
  and the HTTP event API for the life of the `serve` process.
- `ingest/udp_listener.py::TelemetryUDPProtocol._handle` — calls
  `insert_telemetry` / `insert_event`.
- `ingest/event_api.py::post_event` — calls `insert_event`.
- `ingest/vis_watcher.py::VisWatcher.scan_once` — calls `insert_analysis`.
- Every test module — each test gets its own `Store` over a temp-dir SQLite
  file (see `tests/conftest.py::store`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_report.models import AnalysisResult, EventMessage, TelemetryPacket

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
  patrol_id TEXT NOT NULL,
  seq       INTEGER NOT NULL,
  ts_ms     INTEGER NOT NULL,
  zone_id   INTEGER,
  temp_c    REAL,
  humid_pct REAL,
  speed_mps REAL,
  steer     REAL,
  ultra_cm  INTEGER,
  state     TEXT,
  PRIMARY KEY (patrol_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
  patrol_id  TEXT NOT NULL,
  event_seq  INTEGER NOT NULL,
  ts_ms      INTEGER NOT NULL,
  type       TEXT NOT NULL,
  zone_id    INTEGER,
  detail     TEXT,
  PRIMARY KEY (patrol_id, event_seq)
);

CREATE TABLE IF NOT EXISTS analysis (
  patrol_id      TEXT NOT NULL,
  image_id       TEXT NOT NULL,
  captured_at_ms INTEGER NOT NULL,
  image_path     TEXT NOT NULL,
  image_quality  REAL NOT NULL,
  detections     TEXT NOT NULL,
  PRIMARY KEY (patrol_id, image_id)
);
"""


class Store:
    """Thin synchronous wrapper around one SQLite connection.

    One `Store` is shared by both the UDP listener and the HTTP event API
    inside `cli.py::_serve`; since both run as callbacks on the same
    asyncio event loop (single OS thread), there is no concurrent-write
    hazard despite `check_same_thread=False` — that flag only disables
    sqlite3's same-thread assertion so the connection can be created in one
    coroutine and used from callbacks scheduled by the event loop.
    """

    def __init__(self, db_path: Path | str) -> None:
        """Open (or create) the SQLite file at `db_path` and ensure the schema exists.

        `CREATE TABLE IF NOT EXISTS` makes this idempotent — calling it
        against an already-initialised database is a no-op beyond opening
        the connection. Called by every consumer listed in the module
        docstring above.
        """
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection. Called at teardown by
        `cli.py::_serve`'s `finally` block and by every test fixture.
        """
        self._conn.close()

    # --- writes -----------------------------------------------------

    def insert_telemetry(self, pkt: TelemetryPacket) -> bool:
        """Insert one telemetry row; a duplicate `(patrol_id, seq)` is a no-op.

        Returns True if this call actually added a new row, False if the
        row already existed (i.e. this packet is a duplicate delivery).
        Called by `udp_listener.py::TelemetryUDPProtocol._handle` for every
        successfully validated `TELEMETRY` datagram.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO telemetry
                (patrol_id, seq, ts_ms, zone_id, temp_c, humid_pct,
                 speed_mps, steer, ultra_cm, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pkt.patrol_id,
                pkt.seq,
                pkt.ts_ms,
                pkt.zone_id,
                pkt.env.temp_c,
                pkt.env.humid_pct,
                pkt.drive.speed_mps,
                pkt.drive.steer,
                pkt.drive.ultra_cm,
                pkt.drive.state.value,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def insert_event(self, evt: EventMessage) -> bool:
        """Insert one event row; a duplicate `(patrol_id, event_seq)` is a no-op.

        This is what makes event delivery idempotent per ICD §C1.2 — the
        same event arriving twice over HTTP, or up to three times over the
        UDP fallback, only ever produces one stored row. `detail` is
        JSON-encoded since SQLite has no native object column type. Called
        by `event_api.py::post_event` (HTTP path) and
        `udp_listener.py::TelemetryUDPProtocol._handle` (UDP fallback path).
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO events
                (patrol_id, event_seq, ts_ms, type, zone_id, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                evt.patrol_id,
                evt.event_seq,
                evt.ts_ms,
                evt.type.value,
                evt.zone_id,
                json.dumps(evt.detail, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def insert_analysis(self, res: AnalysisResult) -> bool:
        """Insert one VIS analysis row; a duplicate `(patrol_id, image_id)` is a no-op.

        `detections` (a list of `Detection` models) is dumped to a JSON
        array string for storage — `by_alias=True` preserves the `"class"`
        key rather than the Python-safe `class_` attribute name. Called by
        `vis_watcher.py::VisWatcher.scan_once` for every analysis JSON file
        found on disk.
        """
        detections = [d.model_dump(by_alias=True) for d in res.detections]
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO analysis
                (patrol_id, image_id, captured_at_ms, image_path,
                 image_quality, detections)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                res.patrol_id,
                res.image_id,
                res.captured_at_ms,
                res.image_path,
                res.image_quality,
                json.dumps(detections, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # --- reads --------------------------------------------------------

    def received_telemetry_seqs(self, patrol_id: str) -> set[int]:
        """Return every distinct `seq` value actually stored for `patrol_id`.

        Because `seq` is part of the primary key, this set's size is exactly
        the "received" count used in the loss-rate formula. Called by
        `loss_rate` (below) and directly by tests that assert on exactly
        which packets arrived (e.g. out-of-order, dedup).
        """
        rows = self._conn.execute(
            "SELECT seq FROM telemetry WHERE patrol_id = ?", (patrol_id,)
        ).fetchall()
        return {row[0] for row in rows}

    def max_telemetry_seq(self, patrol_id: str) -> int | None:
        """Highest `seq` stored for `patrol_id`, or None if nothing has arrived yet.

        `max(seq) + 1` is ICD §C1.3's definition of the "expected" packet
        count for a patrol. Called by `loss_rate` (below).
        """
        row = self._conn.execute(
            "SELECT MAX(seq) FROM telemetry WHERE patrol_id = ?", (patrol_id,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def loss_rate(self, patrol_id: str) -> float | None:
        """1 - received/expected per ICD §C1.3. None if nothing was received.

        `expected = max_telemetry_seq(patrol_id) + 1`,
        `received = len(received_telemetry_seqs(patrol_id))`. This is the
        exact function the A1 acceptance test
        (`tests/test_a1_acceptance.py`) compares against `fake_rover.py`'s
        configured `--drop-rate`. Called by whatever loads a patrol for
        `pipeline/aggregate.py::aggregate` (A2) to populate `data_completeness`.
        """
        max_seq = self.max_telemetry_seq(patrol_id)
        if max_seq is None:
            return None
        expected = max_seq + 1
        received = len(self.received_telemetry_seqs(patrol_id))
        return 1 - (received / expected)

    def events_for_patrol(self, patrol_id: str) -> list[EventMessage]:
        """Fetch all stored events for `patrol_id`, ordered by `event_seq`.

        Re-parses each stored row back into an `EventMessage` (including
        JSON-decoding `detail`), so callers get the same typed model that
        was originally validated at ingest. Called by whatever loads a
        patrol for `pipeline/segment.py::segment_patrol` (A2) — zone
        boundaries come from these events' `ZONE_ENTER`/`PATROL_START`/
        `PATROL_END` types.
        """
        rows = self._conn.execute(
            """
            SELECT event_seq, ts_ms, type, zone_id, detail
            FROM events WHERE patrol_id = ? ORDER BY event_seq
            """,
            (patrol_id,),
        ).fetchall()
        return [
            EventMessage(
                patrol_id=patrol_id,
                event_seq=r[0],
                ts_ms=r[1],
                type=r[2],
                zone_id=r[3],
                detail=json.loads(r[4]) if r[4] else {},
            )
            for r in rows
        ]

    def analysis_count(self, patrol_id: str) -> int:
        """Count of stored analysis rows for `patrol_id`.

        Used by tests as a cheap way to confirm `VisWatcher` ingestion
        happened; also useful later for deciding whether VIS's `_COMPLETE`
        count matches what was actually stored.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) FROM analysis WHERE patrol_id = ?", (patrol_id,)
        ).fetchone()
        return row[0] if row else 0

    def telemetry_for_patrol(self, patrol_id: str) -> list[TelemetryPacket]:
        """Fetch all stored telemetry for `patrol_id`, ordered by `seq`, as `TelemetryPacket`s.

        Re-parses each stored row back into the full typed model (unlike
        `received_telemetry_seqs`, which only returns bare `seq` numbers) —
        this is the row-level data `pipeline/segment.py::segment_patrol`
        (A2) actually assigns to zones. `zone_id` is read back as whatever
        DR sent; note AI does not trust it for segmentation (ICD §C1.2) even
        though it round-trips here.
        """
        rows = self._conn.execute(
            """
            SELECT seq, ts_ms, zone_id, temp_c, humid_pct, speed_mps, steer, ultra_cm, state
            FROM telemetry WHERE patrol_id = ? ORDER BY seq
            """,
            (patrol_id,),
        ).fetchall()
        return [
            TelemetryPacket(
                patrol_id=patrol_id,
                seq=r[0],
                ts_ms=r[1],
                type="TELEMETRY",
                zone_id=r[2],
                env={"temp_c": r[3], "humid_pct": r[4]},
                drive={"speed_mps": r[5], "steer": r[6], "ultra_cm": r[7], "state": r[8]},
            )
            for r in rows
        ]

    def analysis_for_patrol(self, patrol_id: str) -> list[AnalysisResult]:
        """Fetch all stored VIS analysis for `patrol_id`, ordered by `captured_at_ms`, as `AnalysisResult`s.

        Re-parses each stored row's JSON-encoded `detections` column back
        into typed `Detection` models. This is the row-level data
        `pipeline/segment.py::segment_patrol` (A2) assigns to zones by
        `captured_at_ms`.
        """
        rows = self._conn.execute(
            """
            SELECT image_id, captured_at_ms, image_path, image_quality, detections
            FROM analysis WHERE patrol_id = ? ORDER BY captured_at_ms, image_id
            """,
            (patrol_id,),
        ).fetchall()
        return [
            AnalysisResult(
                image_id=r[0],
                patrol_id=patrol_id,
                captured_at_ms=r[1],
                image_path=r[2],
                image_quality=r[3],
                detections=json.loads(r[4]),
            )
            for r in rows
        ]
