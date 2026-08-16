"""VIS analysis ingest (C2) — watches `data/analysis/{patrol_id}/` for new
per-image JSON and the `_COMPLETE` completion marker.

Unlike the lossy UDP telemetry path, C2 is filesystem delivery and the ICD
gives AI no license to silently drop a bad file: an unknown `state` value
is a contract violation and must raise (error-handling matrix, spec §12),
not be logged and skipped. `scan_once` therefore lets `ValidationError`
propagate.

Called by:
- `devtools/fake_vis.py`'s output is what this module reads back in tests
  (`tests/test_fake_vis.py`) — there is no production wiring yet that calls
  `VisWatcher` automatically; A2's pipeline orchestration (triggered on
  `PATROL_END`) is the intended future caller of `.watch(...)`.
- `tests/test_vis_watcher.py` — exercises `.scan_once` directly against
  hand-written fixture JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ai_report.ingest.store import Store
from ai_report.models import AnalysisResult

logger = logging.getLogger(__name__)

_COMPLETE_MARKER = "_COMPLETE"


@dataclass
class ScanResult:
    """Outcome of one `VisWatcher.scan_once` call.

    `new_records` is how many analysis files were newly inserted this call
    (files already in the store are silently skipped by the dedup, so
    re-scanning the same directory repeatedly is safe and cheap).
    `complete` mirrors whether VIS's `_COMPLETE` marker file exists yet.
    """

    new_records: int
    complete: bool


class VisWatcher:
    """Polls a patrol's analysis directory and ingests any new result files."""

    def __init__(self, store: Store, data_root: Path, poll_interval_s: float = 1.0) -> None:
        """Bind this watcher to `store` and the filesystem root that contains `analysis/`.

        `poll_interval_s` is only used by `.watch` (the polling loop);
        `.scan_once` is a single synchronous pass and ignores it. Called
        wherever a watcher instance is needed — currently only tests; A2's
        orchestration entry point is the intended future production caller.
        """
        self._store = store
        self._data_root = Path(data_root)
        self._poll_interval_s = poll_interval_s

    def scan_once(self, patrol_id: str) -> ScanResult:
        """Read every `*.json` file under `data/analysis/{patrol_id}/` and store new ones.

        For each file: load it as JSON, validate it as an `AnalysisResult`
        (this is where an unknown `state` enum value raises
        `pydantic.ValidationError` and propagates straight out — see the
        module docstring), then hand it to `Store.insert_analysis`, which
        returns False for a file already ingested on a prior scan. Finally
        checks whether VIS's `_COMPLETE` marker file now exists.

        Returns immediately with `new_records=0, complete=False` if the
        patrol's analysis directory doesn't exist yet (VIS hasn't started
        writing). Called by `.watch` (in a loop) and directly by tests.
        """
        analysis_dir = self._data_root / "analysis" / patrol_id
        if not analysis_dir.is_dir():
            return ScanResult(new_records=0, complete=False)

        new_records = 0
        for path in sorted(analysis_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            result = AnalysisResult.model_validate(raw)
            if self._store.insert_analysis(result):
                new_records += 1

        complete = (analysis_dir / _COMPLETE_MARKER).exists()
        return ScanResult(new_records=new_records, complete=complete)

    async def watch(self, patrol_id: str, timeout_s: float | None = None) -> ScanResult:
        """Poll until `_COMPLETE` appears or `timeout_s` elapses.

        Calls `scan_once` in a loop, sleeping `poll_interval_s` between
        attempts (`asyncio.sleep`, so this yields the event loop rather than
        blocking it). If `timeout_s` is given and is exceeded before
        `_COMPLETE` shows up, logs a warning and returns the last
        `ScanResult` anyway — matching ICD §C2.1's "proceed with whatever
        exists and record the gap as a limitation" behaviour rather than
        hanging forever. Called by `tests/test_vis_watcher.py`; intended to
        be called by A2's pipeline orchestration once `PATROL_END` fires.
        """
        elapsed = 0.0
        while True:
            result = self.scan_once(patrol_id)
            if result.complete:
                return result
            if timeout_s is not None and elapsed >= timeout_s:
                logger.warning(
                    "VIS _COMPLETE not observed for patrol_id=%s after %.0fs; proceeding",
                    patrol_id,
                    timeout_s,
                )
                return result
            await asyncio.sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s
