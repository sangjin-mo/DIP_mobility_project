"""Mark a patrol's start/end for `ai_report` around the dashboard's own
START/STOP buttons.

ADR-0009 (`ADR-0009-llm-inferred-crop-zones.md`): `drive/drive_ver2` never
sends `PATROL_START`/`PATROL_END` (or any C1 telemetry/event) to
`ai_report` at all -- confirmed by grep, zero references anywhere in
`drive/`. The dashboard is the one thing in this system that actually
knows when a drive session starts and stops (the user just pressed the
button), so it is what now emits these two events, over `ai_report`'s
existing `POST /api/events` (`ingest/event_api.py`) -- the same contract
`devtools/fake_rover.py::_post_event_http` already exercises, just from a
different sender.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from ai_report.models import EventMessage, EventType

logger = logging.getLogger(__name__)


class PatrolEventService:
    """Posts `PATROL_START`/`PATROL_END` to `ai_report`'s event API.

    Best-effort only: called *after* the real drive command has already
    been accepted by the rover (see `web_dashboard/app.py`'s
    `start_rover`/`stop_rover`), so a failure here must never fail or roll
    back a START/STOP the rover already executed. Every network failure is
    caught and logged, never raised.
    """

    def __init__(self, event_url: str | None, timeout_s: float = 2.0) -> None:
        self._event_url = event_url.strip() if event_url else None
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._active_patrol_id: str | None = None
        self._next_seq = 0

    @property
    def configured(self) -> bool:
        return bool(self._event_url)

    @property
    def active_patrol_id(self) -> str | None:
        with self._lock:
            return self._active_patrol_id

    def start_patrol(self) -> str | None:
        """Generate a fresh `patrol_id` (UTC `YYYYMMDD_HHMM`, matching
        `devtools/fake_rover.py::main`'s own convention) and post
        `PATROL_START`. Returns the new `patrol_id`, or `None` if not
        configured. Called by `app.py`'s `start_rover`, after
        `RoverControlService.send` has already returned successfully.
        """
        if not self.configured:
            return None
        with self._lock:
            patrol_id = datetime.now(UTC).strftime("%Y%m%d_%H%M")
            self._active_patrol_id = patrol_id
            self._next_seq = 0
            self._post(patrol_id, EventType.PATROL_START)
            return patrol_id

    def end_patrol(self) -> str | None:
        """Post `PATROL_END` for the currently active patrol, if any.
        Returns the `patrol_id` it closed, or `None` if not configured or
        nothing was active (e.g. STOP pressed with no prior START in this
        process's lifetime). Called by `app.py`'s `stop_rover`.
        """
        if not self.configured:
            return None
        with self._lock:
            patrol_id = self._active_patrol_id
            if patrol_id is None:
                return None
            self._post(patrol_id, EventType.PATROL_END)
            self._active_patrol_id = None
            return patrol_id

    def _post(self, patrol_id: str, event_type: EventType) -> None:
        """Best-effort `POST /api/events`. Never raises -- see class docstring.

        Builds a real `EventMessage` first (not a hand-assembled dict) so a
        malformed patrol_id or event shape fails loudly here in dev rather
        than silently as a 422 nobody reads the response of.
        """
        event = EventMessage(
            patrol_id=patrol_id,
            event_seq=self._next_seq,
            ts_ms=int(time.time() * 1000),
            type=event_type,
        )
        self._next_seq += 1
        request = urllib.request.Request(
            self._event_url,
            data=json.dumps(event.model_dump(mode="json")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "failed to notify ai_report of %s for patrol_id=%s: %s",
                event_type.value, patrol_id, exc,
            )
