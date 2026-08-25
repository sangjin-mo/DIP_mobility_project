from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from ai_report.ingest.vis_watcher import VisWatcher

PATROL_ID = "20260813_1430"


def _write_analysis(data_root: Path, image_id: str, state: str = "정상", complete: bool = False) -> None:
    analysis_dir = data_root / "analysis" / PATROL_ID
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / f"{image_id}.json").write_text(
        json.dumps(
            {
                "image_id": image_id,
                "patrol_id": PATROL_ID,
                "captured_at_ms": 0,
                "image_path": f"images/{PATROL_ID}/{image_id}.jpg",
                "image_quality": 0.8,
                "detections": [{"class": "tomato", "state": state, "count": 1, "confidence": 0.9}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if complete:
        (analysis_dir / "_COMPLETE").touch()


def test_scan_once_ingests_new_files(tmp_path, store):
    _write_analysis(tmp_path, "z1_001")
    watcher = VisWatcher(store, tmp_path)
    result = watcher.scan_once(PATROL_ID)
    assert result.new_records == 1
    assert result.complete is False
    assert store.analysis_count(PATROL_ID) == 1


def test_scan_once_is_idempotent(tmp_path, store):
    _write_analysis(tmp_path, "z1_001")
    watcher = VisWatcher(store, tmp_path)
    watcher.scan_once(PATROL_ID)
    result = watcher.scan_once(PATROL_ID)
    assert result.new_records == 0  # already stored
    assert store.analysis_count(PATROL_ID) == 1


def test_scan_once_detects_complete_marker(tmp_path, store):
    _write_analysis(tmp_path, "z1_001", complete=True)
    watcher = VisWatcher(store, tmp_path)
    assert watcher.scan_once(PATROL_ID).complete is True


def test_scan_once_missing_patrol_dir(tmp_path, store):
    watcher = VisWatcher(store, tmp_path)
    result = watcher.scan_once(PATROL_ID)
    assert result.new_records == 0
    assert result.complete is False


def test_unknown_state_raises(tmp_path, store):
    _write_analysis(tmp_path, "z1_001", state="병해충")  # not in the closed four-value set
    watcher = VisWatcher(store, tmp_path)
    with pytest.raises(ValidationError):
        watcher.scan_once(PATROL_ID)


async def test_watch_returns_once_complete(tmp_path, store):
    _write_analysis(tmp_path, "z1_001", complete=True)
    watcher = VisWatcher(store, tmp_path, poll_interval_s=0.01)
    result = await watcher.watch(PATROL_ID, timeout_s=1.0)
    assert result.complete is True


def test_complete_is_read_before_the_glob(tmp_path, store):
    """`_COMPLETE` must be sampled before the directory listing, never after.

    classify.py writes every analysis file and only then touches the marker.
    Checking the marker *after* globbing means a scan that listed the
    directory mid-run, then found the marker set moments later, reports
    `complete=True` while silently missing every file written in between --
    and `VisWatcher.watch` returns on that, so those records never reach the
    report.

    Simulated by having the glob itself land one more analysis file and the
    marker, i.e. classify.py finishing in the window between the two reads. A
    correct `scan_once` reports `complete=False` for this pass.
    """
    _write_analysis(tmp_path, "z1_001")
    analysis_dir = tmp_path / "analysis" / PATROL_ID
    watcher = VisWatcher(store, tmp_path)
    real_glob = Path.glob

    def glob_then_finish(self, pattern):
        results = list(real_glob(self, pattern))
        if self == analysis_dir and pattern == "*.json":
            # classify.py lands its last file and the marker right here.
            _write_analysis(tmp_path, "z1_002", complete=True)
        return iter(results)

    with mock.patch.object(Path, "glob", glob_then_finish):
        result = watcher.scan_once(PATROL_ID)

    assert result.complete is False, (
        "marker appeared after the listing, so this pass must not claim completeness"
    )
    assert watcher.scan_once(PATROL_ID).complete is True
    assert store.analysis_count(PATROL_ID) == 2


def test_unreadable_analysis_file_is_skipped_not_fatal(tmp_path, store):
    """A half-written file must cost one more poll, not the whole report.

    `scan_once`'s exceptions propagate into
    `orchestration.py::run_patrol_pipeline`'s broad `except`, which abandons
    the patrol entirely -- and `cli.py`'s trigger dedup then blocks a retry.
    A `ValidationError` (a real contract violation) still propagates; only an
    unparseable file is tolerated.
    """
    _write_analysis(tmp_path, "good", complete=True)
    analysis_dir = tmp_path / "analysis" / PATROL_ID
    (analysis_dir / "partial.json").write_text('{"image_id": "partial", "patr', encoding="utf-8")

    result = VisWatcher(store, tmp_path).scan_once(PATROL_ID)

    assert result.new_records == 1
    assert result.complete is False, "an unread file means this pass is not complete"
    assert store.analysis_count(PATROL_ID) == 1
