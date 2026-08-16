from __future__ import annotations

import json
from pathlib import Path

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
