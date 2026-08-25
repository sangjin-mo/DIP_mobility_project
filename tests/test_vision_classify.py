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

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from pydantic import ValidationError

from ai_report.ingest.vis_watcher import VisWatcher
from vision.image_analysis.system.classify import (
    capture_ts_ms,
    classify_patrol,
    image_id_for,
)

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
    expected = sorted(image_id_for(PATROL_ID, source_dir / n) for n in ("a.jpg", "b.jpg"))
    assert sorted(p.stem for p in images_dir.iterdir()) == expected
    assert sorted(p.stem for p in analysis_dir.glob("*.json")) == expected


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
    only_id = image_id_for(PATROL_ID, source_dir / "only.jpg")
    assert analysis.image_path == f"images/{PATROL_ID}/{only_id}.jpg"
    assert [d.class_ for d in analysis.detections] == ["tomato", "tomato"]


async def test_one_bad_image_does_not_abort_the_batch(tmp_path: Path):
    source_dir = tmp_path / "source"
    _write_fake_source_image(source_dir / "a.jpg", (200, 30, 30))
    _write_fake_source_image(source_dir / "b.jpg", (30, 200, 30))
    data_root = tmp_path / "data"

    # First call raises (simulated API failure on image "a"), second succeeds.
    client = _mock_client(side_effect=[RuntimeError("simulated outage"), _mock_classification_response()])

    # concurrency=1: `side_effect` is consumed in call order, and with several
    # images in flight the file read inside the semaphore can interleave, so
    # which image gets the failure would otherwise be a race. What is under
    # test is that one failure does not abort the batch, not the ordering.
    count = await classify_patrol(
        PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=client, concurrency=1
    )

    assert count == 1  # only the second image made it through
    analysis_dir = data_root / "analysis" / PATROL_ID
    assert (analysis_dir / "_COMPLETE").is_file()  # still written despite the failure
    assert [p.stem for p in analysis_dir.glob("*.json")] == [image_id_for(PATROL_ID, source_dir / "b.jpg")]


async def test_writes_complete_marker_even_with_zero_source_images(tmp_path: Path):
    source_dir = tmp_path / "empty_source"
    source_dir.mkdir()
    data_root = tmp_path / "data"

    count = await classify_patrol(PATROL_ID, source_dir, data_root, model="gpt-5.6-luna", client=_mock_client())

    assert count == 0
    assert (data_root / "analysis" / PATROL_ID / "_COMPLETE").is_file()


async def test_after_before_ts_ms_restricts_to_images_in_that_window(tmp_path: Path):
    """`after_ts_ms`/`before_ts_ms` remain available for manual runs that
    need to re-examine one span of a shared `received/{date}/` directory.

    These filenames carry no capture stamp, so `capture_ts_ms` falls back to
    mtime -- which is what this test manipulates.
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
    assert analysis["image_path"].endswith(f"{image_id_for(PATROL_ID, source_dir / 'during.jpg')}.jpg")


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


async def test_image_ids_are_stable_regardless_of_completion_order(tmp_path: Path):
    """`image_id` must identify the *picture*, not its position in a batch.

    It used to be `{patrol_id}_{index:03d}` over the sorted source list,
    which is only stable while the input set is. Re-running after more frames
    arrived rebound every id to a different image, and
    `Store.insert_analysis`'s `INSERT OR IGNORE` then kept the stale row
    while `shutil.copyfile` overwrote the picture on disk. Deriving the id
    from the filename makes it independent of both batch composition and
    completion order.

    The first image is made the slowest, so with any real parallelism it
    finishes last; its id must still be a.jpg's.
    """
    source_dir = tmp_path / "src"
    for name, color in (("a.jpg", (255, 0, 0)), ("b.jpg", (0, 255, 0)), ("c.jpg", (0, 0, 255))):
        _write_fake_source_image(source_dir / name, color)

    delays = {0: 0.06, 1: 0.0, 2: 0.0}
    calls = {"n": 0}
    completion_order: list[str] = []

    async def slow_first(*args, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        await asyncio.sleep(delays.get(index, 0.0))
        completion_order.append(f"call{index}")
        return _mock_classification_response()

    client = _mock_client(side_effect=slow_first)
    data_root = tmp_path / "data"
    count = await classify_patrol(
        PATROL_ID, source_dir, data_root, "gpt-test", client=client, concurrency=3
    )

    assert count == 3
    assert completion_order[-1] == "call0", "expected the slowest (first-dispatched) call to finish last"

    analysis_dir = data_root / "analysis" / PATROL_ID
    written = sorted(p.stem for p in analysis_dir.glob("*.json"))
    assert written == sorted(image_id_for(PATROL_ID, source_dir / n) for n in ("a.jpg", "b.jpg", "c.jpg"))

    a_id = image_id_for(PATROL_ID, source_dir / "a.jpg")
    first = json.loads((analysis_dir / f"{a_id}.json").read_text(encoding="utf-8"))
    assert first["image_path"] == f"images/{PATROL_ID}/{a_id}.jpg"
    assert first["captured_at_ms"] == capture_ts_ms(source_dir / "a.jpg")


async def test_rerun_skips_already_classified_images(tmp_path: Path):
    """The ledger is what replaced the START/STOP window filter: a second run
    must classify only what arrived since the first, not re-bill the whole
    directory.
    """
    source_dir = tmp_path / "src"
    _write_fake_source_image(source_dir / "a.jpg", (255, 0, 0))
    data_root = tmp_path / "data"

    first_client = _mock_client()
    assert await classify_patrol(
        PATROL_ID, source_dir, data_root, "gpt-test", client=first_client
    ) == 1
    assert first_client.chat.completions.create.await_count == 1

    # A later transfer drops in one more frame.
    _write_fake_source_image(source_dir / "b.jpg", (0, 255, 0))
    second_client = _mock_client()
    assert await classify_patrol(
        PATROL_ID, source_dir, data_root, "gpt-test", client=second_client
    ) == 1
    assert second_client.chat.completions.create.await_count == 1, "a.jpg must not be classified twice"

    analysis_dir = data_root / "analysis" / PATROL_ID
    assert sorted(p.stem for p in analysis_dir.glob("*.json")) == sorted(
        image_id_for(PATROL_ID, source_dir / n) for n in ("a.jpg", "b.jpg")
    )


async def test_reclassify_ignores_the_ledger(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_fake_source_image(source_dir / "a.jpg", (255, 0, 0))
    data_root = tmp_path / "data"

    await classify_patrol(PATROL_ID, source_dir, data_root, "gpt-test", client=_mock_client())
    again = _mock_client()
    assert await classify_patrol(
        PATROL_ID, source_dir, data_root, "gpt-test", client=again, use_ledger=False
    ) == 1
    assert again.chat.completions.create.await_count == 1


async def test_stale_complete_marker_is_cleared_before_reclassifying(tmp_path: Path):
    """A marker left by an earlier run would let `VisWatcher.watch` return
    before this run has written anything at all.
    """
    source_dir = tmp_path / "src"
    _write_fake_source_image(source_dir / "a.jpg", (255, 0, 0))
    data_root = tmp_path / "data"
    analysis_dir = data_root / "analysis" / PATROL_ID
    analysis_dir.mkdir(parents=True)
    marker = analysis_dir / "_COMPLETE"
    marker.touch()
    stale_mtime = marker.stat().st_mtime_ns

    await classify_patrol(PATROL_ID, source_dir, data_root, "gpt-test", client=_mock_client())

    assert marker.is_file()
    assert marker.stat().st_mtime_ns != stale_mtime, "marker should have been recreated, not left in place"


async def test_complete_marker_is_written_even_when_the_run_fails_at_startup(tmp_path: Path):
    """`_COMPLETE` lives in a `finally`. It used to sit after the try, so an
    unset API key or a vanished source directory skipped it entirely and left
    `ai_report` blocking for the whole VIS_COMPLETE_TIMEOUT_S.
    """
    data_root = tmp_path / "data"
    missing_source = tmp_path / "not_there"

    try:
        await classify_patrol(
            PATROL_ID, missing_source, data_root, "gpt-test", client=_mock_client()
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected the missing source directory to propagate")

    assert (data_root / "analysis" / PATROL_ID / "_COMPLETE").is_file()


async def test_concurrency_is_bounded_by_the_semaphore(tmp_path: Path):
    """`concurrency` must actually cap in-flight calls: a patrol can be
    hundreds of frames, and both memory and the API rate limit depend on it.
    """
    source_dir = tmp_path / "src"
    for i in range(6):
        _write_fake_source_image(source_dir / f"img{i}.jpg", (i * 40 % 255, 0, 0))

    in_flight = {"now": 0, "peak": 0}

    async def tracked(*args, **kwargs):
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        await asyncio.sleep(0.02)
        in_flight["now"] -= 1
        return _mock_classification_response()

    client = _mock_client(side_effect=tracked)
    count = await classify_patrol(
        PATROL_ID, source_dir, tmp_path / "data", "gpt-test", client=client, concurrency=2
    )

    assert count == 6
    assert in_flight["peak"] <= 2, f"semaphore breached: {in_flight['peak']} calls in flight"
    assert in_flight["peak"] == 2, "expected the cap to actually be reached"


def _capture_name(stamp: str, seq: int = 1) -> str:
    """A filename in `capture.py::make_filename`'s real format."""
    return f"{stamp}_cam01_{seq:03d}.jpg"


def _expected_ms(stamp: str) -> int:
    return int(datetime.strptime(stamp, "%Y%m%d_%H%M%S").timestamp() * 1000)


def test_capture_ts_ms_reads_the_filename_not_the_mtime(tmp_path: Path):
    """The upload writes files with a fresh mtime (`routes_upload.py` does a
    plain open/write), so mtime is transfer time, not capture time. The
    filename is the only surviving record of when the frame was taken.
    """
    stamp = "20260824_172947"
    src = tmp_path / _capture_name(stamp)
    _write_fake_source_image(src, (10, 20, 30))
    # Simulate the transfer stamping it much later than capture.
    later = _expected_ms(stamp) / 1000 + 3600
    os.utime(src, (later, later))

    assert capture_ts_ms(src) == _expected_ms(stamp)
    assert capture_ts_ms(src) != int(src.stat().st_mtime * 1000)


def test_capture_ts_ms_falls_back_to_mtime_for_an_unstamped_name(tmp_path: Path):
    src = tmp_path / "hand-dropped.jpg"
    _write_fake_source_image(src, (10, 20, 30))
    assert capture_ts_ms(src) == int(src.stat().st_mtime * 1000)


async def test_patrol_window_filters_on_capture_time_not_upload_time(tmp_path: Path):
    """The regression that emptied every report: images are pulled off the Pi
    *after* STOP, so their mtime is later than the patrol's `before_ts_ms` and
    a filter on mtime discarded all of them ("no images found").
    """
    source_dir = tmp_path / "received"
    inside = "20260824_172947"      # within the patrol window
    outside = "20260824_180000"     # a later patrol, same day folder
    for stamp in (inside, outside):
        _write_fake_source_image(source_dir / _capture_name(stamp), (200, 30, 30))

    # Both files land on the PC well after the patrol ended -- the situation
    # that used to filter everything out.
    uploaded_at = _expected_ms("20260824_190000") / 1000
    for p in source_dir.iterdir():
        os.utime(p, (uploaded_at, uploaded_at))

    client = _mock_client()
    data_root = tmp_path / "data"
    count = await classify_patrol(
        PATROL_ID, source_dir, data_root, "gpt-test", client=client,
        after_ts_ms=_expected_ms(inside) - 5_000,
        before_ts_ms=_expected_ms(inside) + 5_000,
    )

    assert count == 1, "the in-window frame must survive despite a much later mtime"
    inside_id = image_id_for(PATROL_ID, source_dir / _capture_name(inside))
    written = json.loads(
        (data_root / "analysis" / PATROL_ID / f"{inside_id}.json").read_text(encoding="utf-8")
    )
    assert written["captured_at_ms"] == _expected_ms(inside)


def _clock(stamp: str) -> str:
    """`20260824_172947` -> `17:29:47`, as `FrameContext.as_prompt_text` renders it."""
    hhmmss = stamp.split("_")[1]
    return f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"


async def test_frame_context_is_sent_with_the_image(tmp_path: Path):
    """The model must be told which frame of the sweep it is looking at, and
    that edge-cut crops are one observation rather than one per fragment.
    """
    source_dir = tmp_path / "received"
    stamps = ["20260824_172947", "20260824_172948", "20260824_172949"]
    for stamp in stamps:
        _write_fake_source_image(source_dir / _capture_name(stamp), (200, 30, 30))

    client = _mock_client()
    await classify_patrol(PATROL_ID, source_dir, tmp_path / "data", "gpt-test", client=client)

    calls = client.chat.completions.create.await_args_list
    assert len(calls) == 3
    texts = []
    for call in calls:
        content = call.kwargs["messages"][1]["content"]
        assert content[0]["type"] == "text", "context must precede the image"
        assert content[1]["type"] == "image_url"
        texts.append(content[0]["text"])

    joined = "\n".join(texts)
    assert "3장" in joined and "1번째" in joined and "3번째" in joined
    assert "17:29:47" in joined, "capture time should come from the filename"
    # Matched by content, not by position: calls complete concurrently, so
    # `await_args_list` order is completion order. What must hold is that the
    # *earliest* frame is the one told it has no predecessor.
    by_time = {stamp[-6:]: next(t for t in texts if _clock(stamp) in t) for stamp in stamps}
    assert "첫 프레임" in by_time["172947"]
    assert sum("첫 프레임" in t for t in texts) == 1, "only the earliest frame has no predecessor"
    # Each later frame is told its gap to the one before it.
    assert "1.0초" in by_time["172948"], "gap to the previous frame"
    assert "1.0초" in by_time["172949"], "gap to the previous frame"
    # The rule that motivated this: a bisected crop is one observation.
    assert "하나의 관측으로" in joined


def test_classification_schema_carries_no_unsupported_keywords():
    """OpenAI's strict structured-output mode rejects numeric bound keywords.

    Pydantic emits them for every `Field(ge=..., le=...)`, which
    `Detection.count`/`confidence` and `ImageClassification.image_quality` all
    use. An unsupported keyword is a 400 on *every* image, and `classify_one`
    logs that as an ordinary per-image skip -- so a total failure would look
    exactly like an empty patrol. The bounds are still enforced on the way
    back in, by `ImageClassification.model_validate_json`.
    """
    from vision.image_analysis.system.classify import _classification_schema

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"), (
                    f"strict mode rejects {key!r}"
                )
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    schema = _classification_schema()
    walk(schema)
    # The strictness that *is* required must survive the strip.
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"image_quality", "detections"}
    assert schema["$defs"]["Detection"]["additionalProperties"] is False


def test_detection_bounds_are_still_enforced_after_parsing():
    """Stripping the bounds from the request schema must not weaken validation."""
    from vision.image_analysis.system.classify import ImageClassification

    with pytest.raises(ValidationError):
        ImageClassification.model_validate(
            {"image_quality": 1.5, "detections": []}
        )


async def test_same_second_frames_get_a_deterministic_order(tmp_path: Path):
    """`capture.py::next_filepath` deliberately supports several frames inside
    one second (seq 001, 002, ...), and the dashboard can drive the interval
    down to 0.2s. A second-resolution stamp alone is therefore neither unique
    nor an ordering key, so `capture_sort_key` pairs it with the sequence.
    """
    source_dir = tmp_path / "received"
    names = [
        "20260824_172947_cam01_003.jpg",
        "20260824_172947_cam01_001.jpg",
        "20260824_172947_cam01_002.jpg",
    ]
    for name in names:
        _write_fake_source_image(source_dir / name, (200, 30, 30))

    client = _mock_client()
    # concurrency=1 so dispatch order is observable; the ordering itself is a
    # property of `capture_sort_key`, not of how many calls are in flight.
    await classify_patrol(
        PATROL_ID, source_dir, tmp_path / "data", "gpt-test", client=client, concurrency=1
    )

    sent = [
        call.kwargs["messages"][1]["content"][0]["text"]
        for call in client.chat.completions.create.await_args_list
    ]
    frame_positions = [text.split("장 가운데 ")[1].split("번째")[0] for text in sent]
    assert frame_positions == ["1", "2", "3"], "frames must be ordered by capture sequence"

    # And the id bound to each frame follows the same order, independent of
    # how the calls happened to complete.
    analysis_dir = tmp_path / "data" / "analysis" / PATROL_ID
    assert sorted(p.stem for p in analysis_dir.glob("*.json")) == sorted(
        image_id_for(PATROL_ID, source_dir / n) for n in names
    )
