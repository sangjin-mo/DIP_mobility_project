"""Tests for `vision/image_analysis/system/classify.py` — the bridge that
fills the gap VIS's own design doc admits it left (see that script's module
docstring): nothing in this repo actually produces C2-contract analysis
JSON from real images.

The most important assertion in this file isn't "does it run" — it's that
what this script writes actually round-trips through the real
`ai_report.ingest.vis_watcher.VisWatcher.scan_once`, the exact function
`orchestration.py::run_patrol_pipeline` calls in production. That's the
concrete proof this bridge produces something `ai_report` can really
consume, not just something that looks plausible. GUIDELINES.md: "No
network calls in any test" — the OpenAI client is always mocked, exactly
like `tests/test_llm_client.py`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from ai_report.ingest.vis_watcher import VisWatcher
from vision.image_analysis.system.classify import classify_patrol

PATROL_ID = "20260824_0900"
_IMAGE_SIZE = (64, 48)


def _write_fake_source_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", _IMAGE_SIZE, color).save(path, "JPEG")


def _mock_classification_response(image_quality: float = 0.8, detections: list[dict] | None = None) -> MagicMock:
    body = {
        "image_quality": image_quality,
        "detections": detections if detections is not None else [
            {"class": "tomato", "state": "정상", "count": 3, "confidence": 0.9},
            {"class": "tomato", "state": "판단불가", "count": 1, "confidence": None},
        ],
    }
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(body, ensure_ascii=False)))]
    return resp


def _mock_client(side_effect=None, return_value=None) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.chat.completions.create = AsyncMock(side_effect=side_effect)
    else:
        client.chat.completions.create = AsyncMock(return_value=return_value or _mock_classification_response())
    client.close = AsyncMock()
    return client


async def test_classifies_every_image_and_writes_complete_marker(tmp_path: Path):
    source_dir = tmp_path / "source"
    _write_fake_source_image(source_dir / "a.jpg", (200, 30, 30))
    _write_fake_source_image(source_dir / "b.jpg", (30, 200, 30))
    data_root = tmp_path / "data"

    count = await classify_patrol(
        PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=_mock_client()
    )

    assert count == 2
    analysis_dir = data_root / "analysis" / PATROL_ID
    images_dir = data_root / "images" / PATROL_ID
    assert (analysis_dir / "_COMPLETE").is_file()
    assert sorted(p.name for p in images_dir.iterdir()) == [f"{PATROL_ID}_000.jpg", f"{PATROL_ID}_001.jpg"]
    assert sorted(p.name for p in analysis_dir.glob("*.json")) == [f"{PATROL_ID}_000.json", f"{PATROL_ID}_001.json"]


async def test_output_round_trips_through_the_real_vis_watcher(tmp_path: Path, store):
    """The real proof this bridge is contract-compliant: feed its output
    straight into `VisWatcher.scan_once`, the exact function
    `orchestration.py::run_patrol_pipeline` calls in production, and
    confirm it ingests cleanly (no ValidationError, real rows in `store`).
    """
    source_dir = tmp_path / "source"
    _write_fake_source_image(source_dir / "only.jpg", (10, 10, 200))
    data_root = tmp_path / "data"

    await classify_patrol(PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=_mock_client())

    watcher = VisWatcher(store, data_root)
    result = watcher.scan_once(PATROL_ID)

    assert result.new_records == 1
    assert result.complete is True
    assert store.analysis_count(PATROL_ID) == 1
    [analysis] = store.analysis_for_patrol(PATROL_ID)
    assert analysis.image_path == f"images/{PATROL_ID}/{PATROL_ID}_000.jpg"
    assert [d.class_ for d in analysis.detections] == ["tomato", "tomato"]


async def test_one_bad_image_does_not_abort_the_batch(tmp_path: Path):
    source_dir = tmp_path / "source"
    _write_fake_source_image(source_dir / "a.jpg", (200, 30, 30))
    _write_fake_source_image(source_dir / "b.jpg", (30, 200, 30))
    data_root = tmp_path / "data"

    # First call raises (simulated API failure on image "a"), second succeeds.
    client = _mock_client(side_effect=[RuntimeError("simulated outage"), _mock_classification_response()])

    count = await classify_patrol(PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=client)

    assert count == 1  # only the second image made it through
    analysis_dir = data_root / "analysis" / PATROL_ID
    assert (analysis_dir / "_COMPLETE").is_file()  # still written despite the failure
    assert [p.name for p in analysis_dir.glob("*.json")] == [f"{PATROL_ID}_001.json"]


async def test_writes_complete_marker_even_with_zero_source_images(tmp_path: Path):
    source_dir = tmp_path / "empty_source"
    source_dir.mkdir()
    data_root = tmp_path / "data"

    count = await classify_patrol(PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=_mock_client())

    assert count == 0
    assert (data_root / "analysis" / PATROL_ID / "_COMPLETE").is_file()


async def test_after_before_ts_ms_restricts_to_images_in_that_window(tmp_path: Path):
    """A shared `received/{date}/` directory can hold more than one patrol's
    images; `after_ts_ms`/`before_ts_ms` (web_dashboard's own recorded
    START/END epoch-ms) must pick out only this patrol's own files by mtime.
    """
    source_dir = tmp_path / "source"
    _write_fake_source_image(source_dir / "before.jpg", (200, 30, 30))
    _write_fake_source_image(source_dir / "during.jpg", (30, 200, 30))
    _write_fake_source_image(source_dir / "after.jpg", (30, 30, 200))
    data_root = tmp_path / "data"

    now = time.time()
    os.utime(source_dir / "before.jpg", (now - 100, now - 100))
    os.utime(source_dir / "during.jpg", (now - 50, now - 50))
    os.utime(source_dir / "after.jpg", (now, now))

    count = await classify_patrol(
        PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=_mock_client(),
        after_ts_ms=int((now - 60) * 1000), before_ts_ms=int((now - 40) * 1000),
    )

    assert count == 1
    analysis_dir = data_root / "analysis" / PATROL_ID
    [analysis_path] = analysis_dir.glob("*.json")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert analysis["image_path"].endswith("_000.jpg")


async def test_confidence_required_unless_undetermined_is_enforced(tmp_path: Path):
    """If the model returns a non-null confidence-less normal detection
    (violating `Detection`'s own validator), that image is skipped rather
    than silently accepted with a contract-violating row.
    """
    source_dir = tmp_path / "source"
    _write_fake_source_image(source_dir / "a.jpg", (200, 30, 30))
    data_root = tmp_path / "data"

    bad_response = _mock_classification_response(
        detections=[{"class": "tomato", "state": "정상", "count": 1, "confidence": None}]
    )
    count = await classify_patrol(
        PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=_mock_client(return_value=bad_response)
    )

    assert count == 0
    assert (data_root / "analysis" / PATROL_ID / "_COMPLETE").is_file()
