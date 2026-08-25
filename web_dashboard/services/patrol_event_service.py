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
from collections.abc import Callable
from datetime import datetime
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
        transfer_images: Callable[[], dict] | None = None,
    ) -> None:
        self._event_url = event_url.strip() if event_url else None
        self._timeout_s = timeout_s
        self._auto_classify_enabled = auto_classify_enabled
        # Injected rather than constructed here so this service keeps no hard
        # dependency on VisionCaptureService (and so tests can drive the
        # ordering without a VIS server). `app.py` passes
        # `VisionCaptureService.transfer`.
        self._transfer_images = transfer_images
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
        self._last_patrol_id: str | None = None
        # Monotonic for the life of the process, never reset per patrol.
        # Resetting it meant a second patrol in the same minute -- same
        # patrol_id -- re-posted event_seq=0, which `Store.insert_event`
        # dedups on (patrol_id, event_seq). Its PATROL_END was therefore
        # dropped as a duplicate and `event_api.py` never fired
        # `on_patrol_end`, so that patrol got no report at all.
        self._next_seq = 0

    @property
    def configured(self) -> bool:
        return bool(self._event_url)

    @property
    def active_patrol_id(self) -> str | None:
        with self._lock:
            return self._active_patrol_id

    def start_patrol(self) -> str | None:
        """Generate a fresh `patrol_id` (local `YYYYMMDD_HHMM`) and post
        `PATROL_START`. Returns the new `patrol_id`, or `None` if not
        configured. Called by `app.py`'s `start_rover`, after
        `RoverControlService.send` has already returned successfully.

        Local time, not UTC. The patrol_id is not just an identifier:
        `ai_report/pipeline/aggregate.py::_patrol_date` slices the report's
        own date straight out of its `YYYYMMDD` prefix, and it names the
        report directory an operator browses. Everything it has to line up
        with is local — `capture.py` stamps filenames with `datetime.now()`,
        `_trigger_classification` reads `received/{local date}/`. Minting it
        in UTC put a KST operator's 17:32 patrol in `20260824_0832` and, for
        anything before 09:00, dated the report to the previous day.
        """
        if not self.configured:
            return None
        with self._lock:
            # DTZ005 is suppressed deliberately: local time is the point here,
            # not an oversight -- see the docstring above.
            patrol_id = datetime.now().strftime("%Y%m%d_%H%M")  # noqa: DTZ005
            if patrol_id == self._last_patrol_id:
                # C3's schema fixes patrol_id at YYYYMMDD_HHMM (13 chars), so
                # two patrols inside one minute genuinely cannot be told
                # apart: they share a report directory, an images/ directory
                # and an analysis/ directory. Nothing here can prevent that
                # without breaking the contract, so make it loud rather than
                # silent -- the previous patrol's report is about to be
                # replaced by this one's.
                logger.warning(
                    "patrol_id=%s reuses the previous patrol's id (same minute); "
                    "its report and analysis output will be overwritten",
                    patrol_id,
                )
            self._active_patrol_id = patrol_id
            self._last_patrol_id = patrol_id
            self._active_patrol_started_ms = int(time.time() * 1000)
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
        """Pull this patrol's images off the Pi, then classify them — on a
        background thread, so STOP returns immediately.

        The transfer has to happen first and has to be waited for. Images
        live on the webcam Pi until something calls its `/trigger-upload`,
        and nothing in the STOP path used to do that: classification ran
        against whatever happened to be in `received/{today}/` already, which
        for this patrol was nothing. Every auto-triggered run in `logs/`
        recorded `classified 0/0 image(s)`, and every resulting report had
        zero zones.

        Threaded rather than inline because `VisionCaptureService.transfer`
        blocks until the Pi has finished uploading every pending frame — up
        to its own 35s timeout — and the STOP button must not wait on that.
        `ai_report` is already polling for `_COMPLETE` by this point (the
        `PATROL_END` post above started its `VIS_COMPLETE_TIMEOUT_S` clock),
        so transfer and classification both have to fit inside that budget;
        see `config.py`'s note on how it is sized.

        Never raises: this runs detached, and a transfer or spawn failure
        must not disturb a STOP the rover has already executed.
        """
        thread = threading.Thread(
            target=self._transfer_then_classify,
            args=(patrol_id, started_ms, ended_ms),
            name=f"auto-classify-{patrol_id}",
            daemon=True,
        )
        thread.start()

    def _transfer_then_classify(self, patrol_id: str, started_ms: int | None, ended_ms: int) -> None:
        """Body of `_trigger_classification`, run on its own thread."""
        self._pull_pending_images(patrol_id)
        self._spawn_classify(patrol_id, started_ms, ended_ms)

    def _pull_pending_images(self, patrol_id: str) -> None:
        """Ask VIS to upload everything still pending on the Pi, and wait.

        Best-effort: if no transfer callable was injected, or the Pi is
        unreachable, classification still runs against whatever already
        landed. Logging the outcome matters because "0 images classified" is
        otherwise indistinguishable between "nothing was captured" and "the
        transfer never happened".
        """
        if self._transfer_images is None:
            logger.info(
                "auto-classify for patrol_id=%s: no transfer hook configured, "
                "classifying only images already received",
                patrol_id,
            )
            return
        try:
            result = self._transfer_images()
        except Exception as exc:  # noqa: BLE001 - VIS errors must not escape a detached thread
            logger.warning(
                "image transfer failed before classifying patrol_id=%s: %s; "
                "proceeding with images already received",
                patrol_id, exc,
            )
            return
        logger.info(
            "image transfer for patrol_id=%s: requested=%s success=%s failed=%s",
            patrol_id,
            result.get("requested") if isinstance(result, dict) else None,
            result.get("success") if isinstance(result, dict) else None,
            result.get("failed") if isinstance(result, dict) else None,
        )

    def _spawn_classify(self, patrol_id: str, started_ms: int | None, ended_ms: int) -> None:
        """Fire-and-forget `python -m vision.image_analysis.system.classify`.

        `source_dir` is today's `received/{YYYY-MM-DD}/` directory (the
        vision PC server's own layout, `pc_server/routes_upload.py`'s
        `day_dir_from_filename`), shared by every patrol that ran today.

        No `--after-ts-ms`/`--before-ts-ms` is passed. Scoping by the drive
        window was wrong on this system: `INTEGRATION_RUNBOOK.md` states that
        camera capture is independent of vehicle control, so the frames a
        patrol should report on are simply the ones that had not been
        classified yet, not the ones whose capture time happens to fall
        between START and STOP. classify.py's ledger enforces that instead
        (see `classify_patrol`), which also makes a re-run pick up exactly
        what a late transfer added. `started_ms`/`ended_ms` are kept in the
        signature and logged, because they remain the honest record of when
        the patrol actually ran.

        Does not handle a patrol that spans midnight (rare for a short
        patrol; the images would fall in yesterday's folder and be missed).

        Never raises and never blocks: `Popen` returns as soon as the
        subprocess is spawned, and any failure inside classify.py itself only
        ever reaches its own log file.
        """
        source_dir = self._received_root / datetime.now().strftime("%Y-%m-%d")
        if not source_dir.is_dir():
            logger.warning(
                "auto-classify skipped for patrol_id=%s: %s does not exist yet",
                patrol_id, source_dir,
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
                    ],
                    cwd=str(_REPO_ROOT),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                "auto-classify started for patrol_id=%s (source=%s, log=%s, patrol ran %s-%s)",
                patrol_id, source_dir, log_path, started_ms, ended_ms,
            )
        except OSError as exc:
            logger.warning("failed to start auto-classify for patrol_id=%s: %s", patrol_id, exc)
