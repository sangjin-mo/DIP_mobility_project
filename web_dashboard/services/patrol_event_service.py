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
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from ai_report.models import EventMessage, EventType

logger = logging.getLogger(__name__)

# repo_root/web_dashboard/services/patrol_event_service.py -> repo_root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECEIVED_ROOT = _REPO_ROOT / "vision" / "image_transfer" / "system" / "pc_server" / "received"
_LOG_DIR = _REPO_ROOT / "logs"


class PatrolEventService:
    """Posts `PATROL_START`/`PATROL_END` to `ai_report`'s event API, and
    (when `auto_classify_enabled`) kicks off classification for the images
    that patrol just captured.

    Best-effort only: called *after* the real drive command has already
    been accepted by the rover (see `web_dashboard/app.py`'s
    `start_rover`/`stop_rover`), so a failure here must never fail or roll
    back a START/STOP the rover already executed. Every network failure is
    caught and logged, never raised. The classify.py subprocess (spawned by
    `end_patrol`) is launched the same way -- fire-and-forget, its own
    failures only ever reach a log file, never this process.
    """

    def __init__(
        self,
        event_url: str | None,
        timeout_s: float = 2.0,
        auto_classify_enabled: bool = True,
        received_root: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._event_url = event_url.strip() if event_url else None
        self._timeout_s = timeout_s
        self._auto_classify_enabled = auto_classify_enabled
        # Overridable so tests can point these at a tmp_path instead of the
        # real repo's received/ and logs/ directories -- see
        # _trigger_classification, which would otherwise spawn a real
        # classify.py subprocess (and a real OpenAI call) against whatever
        # happens to be in today's received/ folder when tests run.
        self._received_root = received_root or _RECEIVED_ROOT
        self._log_dir = log_dir or _LOG_DIR
        self._lock = threading.Lock()
        self._active_patrol_id: str | None = None
        self._active_patrol_started_ms: int | None = None
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
            self._active_patrol_started_ms = int(time.time() * 1000)
            self._next_seq = 0
            self._post(patrol_id, EventType.PATROL_START)
            return patrol_id

    def end_patrol(self) -> str | None:
        """Post `PATROL_END` for the currently active patrol, if any, then
        (if the post succeeded and auto-classify is enabled) kick off
        classification of the images this patrol just captured. Returns the
        `patrol_id` it closed, or `None` if not configured or nothing was
        active (e.g. STOP pressed with no prior START in this process's
        lifetime). Called by `app.py`'s `stop_rover`.
        """
        if not self.configured:
            return None
        with self._lock:
            patrol_id = self._active_patrol_id
            started_ms = self._active_patrol_started_ms
            if patrol_id is None:
                return None
            ended_ms = int(time.time() * 1000)
            posted = self._post(patrol_id, EventType.PATROL_END)
            self._active_patrol_id = None
            self._active_patrol_started_ms = None
        if posted and self._auto_classify_enabled:
            self._trigger_classification(patrol_id, started_ms, ended_ms)
        return patrol_id

    def _post(self, patrol_id: str, event_type: EventType) -> bool:
        """Best-effort `POST /api/events`. Never raises -- see class docstring.

        Builds a real `EventMessage` first (not a hand-assembled dict) so a
        malformed patrol_id or event shape fails loudly here in dev rather
        than silently as a 422 nobody reads the response of. Returns whether
        the post actually succeeded, so `end_patrol` can skip triggering
        classification when `ai_report` was unreachable (it would never
        pick up the resulting analysis files anyway -- it never saw
        `PATROL_END`).
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
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "failed to notify ai_report of %s for patrol_id=%s: %s",
                event_type.value, patrol_id, exc,
            )
            return False

    def _trigger_classification(self, patrol_id: str, started_ms: int | None, ended_ms: int) -> None:
        """Fire-and-forget `python -m vision.image_analysis.system.classify`
        against this patrol's own images.

        `source_dir` is today's `received/{YYYY-MM-DD}/` directory (the
        vision PC server's own layout, `pc_server/routes_upload.py`'s
        `day_dir_from_filename`) -- shared by every patrol that ran today,
        which is exactly why `--after-ts-ms`/`--before-ts-ms` (this
        patrol's own START/END epoch-ms) are passed: they scope
        classification to just this patrol's images by file mtime, even
        though they all live in the same folder. Does not handle a patrol
        that spans midnight (rare for a short patrol; the images would fall
        in yesterday's folder and be missed) -- not worth the complexity
        for that edge case.

        Never raises and never blocks the caller: `Popen` returns as soon as
        the subprocess is spawned, and any failure inside classify.py itself
        (bad API key, network error, zero images) only ever reaches its own
        log file, exactly like a manual run would.
        """
        source_dir = self._received_root / datetime.now().strftime("%Y-%m-%d")
        if not source_dir.is_dir():
            logger.warning(
                "auto-classify skipped for patrol_id=%s: %s does not exist yet",
                patrol_id, source_dir,
            )
            return
        if started_ms is None:
            logger.warning(
                "auto-classify skipped for patrol_id=%s: no recorded start time", patrol_id
            )
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / f"classify_{patrol_id}.log"
        try:
            with open(log_path, "wb") as log_file:
                subprocess.Popen(
                    [
                        sys.executable, "-m", "vision.image_analysis.system.classify",
                        "--patrol-id", patrol_id,
                        "--source-dir", str(source_dir),
                        "--after-ts-ms", str(started_ms),
                        "--before-ts-ms", str(ended_ms),
                    ],
                    cwd=str(_REPO_ROOT),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                "auto-classify started for patrol_id=%s (source=%s, log=%s)",
                patrol_id, source_dir, log_path,
            )
        except OSError as exc:
            logger.warning("failed to start auto-classify for patrol_id=%s: %s", patrol_id, exc)
