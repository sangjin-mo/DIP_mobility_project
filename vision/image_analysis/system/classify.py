"""Bridge: classify captured crop images into VIS's C2 analysis contract
using a vision-capable LLM.

Why this exists: VIS's own design doc
(`vision/image_analysis/design/README.md` §2-3) confirms VIS does not run
its own crop-detection model (the originally-planned YOLO-World/Florence-2
path was never built) and instead expects "다른 담당자의 LLM API" (the LLM
API someone else owns) to do the classification. Nothing in this repo
filled that role, so `data/analysis/{patrol_id}/` never gets written and
`ai_report`'s pipeline never has real per-image detections to work with.

This script fills exactly that gap, and only that gap:
- It does not touch `ai_report/` — GUIDELINES.md ("The AI subsystem never
  runs a model on an image") is a rule about `ai_report`'s own code, not
  about whether classification happens at all. This lives outside
  `ai_report/`, standing in for VIS's undelivered classifier.
- It does not touch `vision/image_transfer/` — VIS's actual owned scope
  (capture + transfer) is untouched; this only reads already-received
  files.
- It reuses `ai_report.models.AnalysisResult`/`Detection` directly rather
  than hand-writing a parallel schema (GUIDELINES.md: "Boundary models are
  generated from contracts/schemas/, not hand-written" — these already are
  that generated model, and reusing them is what keeps this script's
  output from silently drifting off the C2 contract
  `01-interface-contracts.md` §C2.2 / `contracts/schemas/c2-analysis.schema.json`
  define).

Produces, for every not-yet-classified `*.jpg`/`*.jpeg` in `--source-dir`:
- `{data_root}/images/{patrol_id}/{image_id}.jpg` (C2.1's required raw-image
  location)
- `{data_root}/analysis/{patrol_id}/{image_id}.json` (one `AnalysisResult`
  per image)
then a final `{data_root}/analysis/{patrol_id}/_COMPLETE` marker — the
exact signal `ai_report.ingest.vis_watcher.VisWatcher.watch` polls for.

Usage:
    python -m vision.image_analysis.system.classify \\
        --patrol-id 20260824_0900 \\
        --source-dir vision/image_transfer/system/pc_server/received/2026-08-24

`--patrol-id` is required, not auto-detected: nothing in the captured
filenames (`received/{date}/{ts}_cam01_{seq}.jpg`) ties an image to a
specific patrol's telemetry window, and guessing that mapping wrongly would
silently corrupt zone assignment (`pipeline/segment.py`'s job, and the
single most correctness-critical piece of the whole AI subsystem per
GUIDELINES.md hard rule 4) — so this script requires a human or an upstream
caller to say which patrol a batch of images belongs to, the same way
`devtools/fake_vis.py --patrol-id` does.

Which images belong to that patrol is decided by a ledger of what has
already been classified, not by the patrol's own START/STOP window — see
`classify_patrol` for why the window filter was the wrong question on this
system. Re-running is idempotent: `image_id` is derived from the source
filename, so the same picture always lands under the same id.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from ai_report.config import get_settings
from ai_report.llm.schema import _make_strict
from ai_report.models import AnalysisResult, Detection

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".jpg", ".jpeg")

# Name of the cross-patrol ledger that records which source files have already
# been classified, so a re-run picks up only what is new (see `_load_ledger`).
LEDGER_NAME = ".classified.json"

# `capture.py::make_filename` names every frame `YYYYMMDD_HHMMSS_{cam}_{seq}.jpg`
# in the Pi's local time. That name is the only surviving record of when a frame
# was actually taken: `pc_server/routes_upload.py` used to write uploads with a
# plain `open(...).write(...)`, giving every file in one transfer batch the same
# mtime -- the moment it landed on the PC, often minutes after capture. That
# route now restores the capture time with `os.utime`, but this parse stays the
# primary source: it is correct even for files uploaded before that fix.
#
# `seq` matters as well as `stamp`. `capture.py::next_filepath` deliberately
# supports several frames inside one second (seq 001, 002, ...) and the
# dashboard can drive the interval down to MIN_CAPTURE_INTERVAL_SEC = 0.2, so
# the second-resolution stamp alone is not a unique -- or even an ordering --
# key. `capture_sort_key` pairs the two; `capture_ts_ms` remains
# second-resolution because that is genuinely all the filename records, and
# inventing sub-second precision would be fabricating data.
_CAPTURE_NAME_RE = re.compile(r"^(?P<stamp>\d{8}_\d{6})_(?P<cam>[^_]+)_(?P<seq>\d+)")


def capture_ts_ms(path: Path) -> int:
    """Epoch-ms this frame was captured, read from its filename.

    Falls back to mtime for any name that doesn't carry a stamp (a
    hand-dropped file, a different camera's convention), which is still
    better than refusing to classify the image.

    The stamp has no timezone, so it is interpreted in this machine's local
    time -- correct as long as the Pi and the PC agree on a timezone, which
    they do today (both KST). `check_timezone_alignment` logs a warning when
    the parsed capture time lands implausibly far from the file's own mtime,
    which is what a Pi/PC timezone mismatch looks like from here.
    """
    match = _CAPTURE_NAME_RE.match(path.name)
    if match is None:
        logger.debug("no capture stamp in %s; falling back to mtime", path.name)
        return int(path.stat().st_mtime * 1000)
    try:
        parsed = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")
    except ValueError:
        logger.warning("unparseable capture stamp in %s; falling back to mtime", path.name)
        return int(path.stat().st_mtime * 1000)
    return int(parsed.timestamp() * 1000)


def capture_sort_key(path: Path) -> tuple[int, int, str]:
    """Total order over one patrol's frames: capture second, then capture
    sequence within that second, then filename as a final tiebreak.

    Sorting on `capture_ts_ms` alone is ambiguous for frames saved inside the
    same second, which `capture.py` produces routinely at any interval below
    1.0s. An ambiguous order would make `image_id` assignment -- and therefore
    the whole report -- non-deterministic, which GUIDELINES.md hard rule 2
    forbids. Called by `classify_patrol` when ordering `sources`.
    """
    match = _CAPTURE_NAME_RE.match(path.name)
    seq = int(match.group("seq")) if match else 0
    return (capture_ts_ms(path), seq, path.name)


# A frame cannot have been captured meaningfully *after* the file holding it
# was written, so a positive skew that large is not a slow upload -- it is a
# clock or timezone disagreement. Only that direction is checked: a capture
# time long *before* the mtime is the normal case (the image sat on the Pi
# until someone triggered a transfer), so it says nothing either way. A few
# minutes of slack absorbs ordinary clock drift between the two machines.
_TZ_SKEW_WARN_MS = 5 * 60 * 1000


def check_timezone_alignment(sources: list[Path], capture_times: list[int]) -> None:
    """Warn when a parsed capture time sits implausibly *after* its file's mtime.

    `capture_ts_ms` resolves the Pi's naive filename stamp against *this*
    machine's timezone. Run the dashboard on a UTC host while the Pi writes
    KST names and every capture time shifts by hours -- previously a silent
    wrong answer that just looked like "no images found". Purely advisory: it
    logs once and never raises. Called by `classify_patrol` once per run.
    """
    for src, captured_ms in zip(sources, capture_times):
        try:
            mtime_ms = int(src.stat().st_mtime * 1000)
        except OSError:
            continue
        skew_ms = captured_ms - mtime_ms
        if skew_ms > _TZ_SKEW_WARN_MS:
            logger.warning(
                "capture time parsed from %s is %.1fh later than the file was written; "
                "check that this machine and the capture Pi share a timezone",
                src.name, skew_ms / 3_600_000,
            )
            return


def image_id_for(patrol_id: str, source: Path) -> str:
    """Stable `image_id` for one source file within one patrol.

    Derived from the source filename, not from its position in a sorted list.
    Positional ids (`{patrol_id}_{index:03d}`) are only stable while the input
    set is, so re-running after more frames arrived silently rebound every id
    to a different picture -- and `Store.insert_analysis`'s `INSERT OR IGNORE`
    on `(patrol_id, image_id)` then kept the *stale* row while
    `shutil.copyfile` overwrote the image on disk. A filename-derived id makes
    re-runs idempotent instead. Nothing in `ai_report` parses `image_id`; it is
    an opaque key (`devtools/fake_vis.py` already uses a different scheme
    entirely), so its shape is free.

    Non-alphanumerics are collapsed to `_` to satisfy
    `web_dashboard/services/report_service.py`'s `_IMAGE_ID_PATTERN`.
    """
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", source.stem)
    return f"{patrol_id}_{stem}"

SYSTEM_PROMPT = (
    "당신은 온실 작물 순찰 사진 한 장을 분석하는 시스템입니다. "
    "사진에 보이는 작물을 종류별로 세어, 각 관측의 생육 상태를 다음 네 가지 중 "
    "하나로만 분류하세요: 정상, 미성숙, 병충해_의심, 판단불가. "
    "같은 사진 안에서 같은 종류+상태의 관측은 하나의 항목으로 묶고 count로 개수를 "
    "표시하세요. 같은 개체가 여러 장에 반복해서 찍혀도 이 사진 한 장 기준으로만 "
    "세십시오 (관측 수이지 개체 수가 아닙니다). 판단불가로 분류한 경우에만 "
    "confidence를 null로 두고, 그 외에는 반드시 0에서 1 사이의 confidence를 "
    "제시하세요. 작물이 전혀 보이지 않으면 detections를 빈 배열로 반환하세요. "
    "image_quality는 사진의 선명도·조명·구도가 판정에 충분한지를 0(전혀 사용 "
    "불가)에서 1(매우 선명) 사이로 평가한 값입니다."
)


class ImageClassification(BaseModel):
    """Structured-output shape for one image.

    Wraps `ai_report.models.Detection` directly (see module docstring for
    why) rather than a hand-rolled parallel type. Called by
    `_classification_schema` (to build the API request) and
    `classify_image` (to parse the response).
    """

    model_config = ConfigDict(extra="forbid")

    image_quality: float = Field(ge=0, le=1)
    detections: list[Detection]


# JSON Schema validation keywords that OpenAI's strict structured-output mode
# does not accept. Pydantic emits them for any `Field(ge=..., le=...)`, which
# `Detection.count`/`confidence` and `ImageClassification.image_quality` all
# use. `ai_report/llm/schema.py`'s own report schema never hit this because
# `LlmReportOutput` deliberately has no numeric field anywhere; this schema
# does, and an unsupported keyword is a 400 on *every* image -- which
# `classify_one` would log as an ordinary per-image skip, making a total
# failure look like an empty patrol.
#
# Dropping them costs nothing: the response is parsed back through
# `ImageClassification.model_validate_json`, so Pydantic still enforces every
# bound. The schema only has to describe the shape well enough for the model.
_STRICT_UNSUPPORTED_KEYWORDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")


def _strip_unsupported_keywords(schema: dict) -> dict:
    """Recursively remove `_STRICT_UNSUPPORTED_KEYWORDS` from a schema dict.

    Walks `properties`, `items`, `$defs`/`definitions` and the `anyOf`/`allOf`/
    `oneOf` branches -- `confidence: float | None` puts its bounds inside an
    `anyOf` arm, which a properties-only walk would miss. Called by
    `_classification_schema`.
    """
    for keyword in _STRICT_UNSUPPORTED_KEYWORDS:
        schema.pop(keyword, None)
    for key in ("properties", "$defs", "definitions"):
        for sub_schema in schema.get(key, {}).values():
            _strip_unsupported_keywords(sub_schema)
    if "items" in schema:
        _strip_unsupported_keywords(schema["items"])
    for key in ("anyOf", "allOf", "oneOf"):
        for sub_schema in schema.get(key, []):
            _strip_unsupported_keywords(sub_schema)
    return schema


def _classification_schema() -> dict:
    """Strict JSON schema for the API's `response_format`, reusing the same
    `_make_strict` helper `ai_report/llm/schema.py::output_json_schema`
    uses — that function is generic (any Pydantic-generated schema dict in,
    same dict out), so there is nothing AI-report-specific about importing
    it here. Called by `classify_image`.

    `_strip_unsupported_keywords` then removes the numeric bounds Pydantic
    emits, which strict mode rejects — see that constant's comment.
    """
    return _strip_unsupported_keywords(
        _make_strict(ImageClassification.model_json_schema(by_alias=True))
    )


def _load_ledger(path: Path) -> dict[str, dict]:
    """Read the already-classified ledger, or `{}` if it doesn't exist yet.

    Keys are `{day}/{filename}` relative paths into the shared `received/`
    tree; values record which patrol claimed the file and under which
    `image_id`. A corrupt ledger is treated as empty and logged rather than
    raised on — losing the ledger costs a re-classification, while refusing to
    start costs the patrol its whole report.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read classification ledger at %s; treating as empty", path)
        return {}
    return data if isinstance(data, dict) else {}


def _save_ledger(path: Path, ledger: dict[str, dict]) -> None:
    """Write the ledger atomically (temp file + `os.replace`).

    Atomic because a concurrent classify run — or a crash mid-write — must
    never leave a half-written ledger that `_load_ledger` then discards,
    silently re-classifying (and re-billing) every image in the tree.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("could not write classification ledger at %s: %s", path, exc)
        tmp.unlink(missing_ok=True)


def _ledger_key(source: Path) -> str:
    """`{day}/{filename}` — how one source file is identified in the ledger.

    Includes the day directory so two frames with the same name on different
    days (a Pi whose clock reset, say) stay distinct entries.
    """
    return f"{source.parent.name}/{source.name}"


class FrameContext(BaseModel):
    """Where one frame sits in its patrol's capture sweep.

    Passed to the model as text alongside the image so it knows it is looking
    at one frame of a continuous 1-per-second sweep rather than an isolated
    photo. Without it, a crop bisected by the frame edge reads as an ordinary
    partial crop with no explanation, and there is no signal at all that the
    neighbouring frames overlap heavily.

    This does not let the model deduplicate across frames -- each call still
    sees exactly one image, and per ADR-0006 the output stays 관측 수, not
    개체 수. It only makes each independent judgement better informed.
    """

    model_config = ConfigDict(extra="forbid")

    index: int          # 0-based position in the patrol's sorted frames
    total: int
    captured_at_ms: int
    gap_s: float | None = None   # seconds since the previous frame, None for the first

    def as_prompt_text(self) -> str:
        when = datetime.fromtimestamp(self.captured_at_ms / 1000).strftime("%H:%M:%S")
        gap = "이 순찰의 첫 프레임입니다." if self.gap_s is None else (
            f"직전 프레임과의 간격은 약 {self.gap_s:.1f}초입니다."
        )
        return (
            f"이 사진은 순찰 중 연속 촬영된 {self.total}장 가운데 "
            f"{self.index + 1}번째 프레임이며, 촬영 시각은 {when}입니다. {gap} "
            "로버가 이동하면서 촬영하므로 인접한 프레임끼리는 화면이 크게 겹치고, "
            "한 개체가 프레임 경계에 걸려 잘린 채로 보이는 경우가 많습니다. "
            "프레임 가장자리에서 잘린 작물은 잘린 조각마다 따로 세지 말고 "
            "하나의 관측으로 세십시오. 여전히 이 사진 한 장에 보이는 것만 "
            "판단하고, 다른 프레임에 무엇이 찍혔을지는 추측하지 마십시오."
        )


async def classify_image(
    client: AsyncOpenAI,
    image_bytes: bytes,
    model: str,
    timeout_s: float,
    context: FrameContext | None = None,
) -> ImageClassification:
    """One vision LLM call for one image's raw bytes.

    `context`, when given, is sent as a text block before the image so the
    model knows which frame of the sweep it is looking at (see
    `FrameContext`). It is optional so this stays callable for a one-off
    image with no patrol around it.

    Raises on any failure (API error, schema-invalid response, or a
    `Detection` that violates its own `confidence`-required-unless-판단불가
    rule) — deliberately, unlike `ai_report/llm/client.py::generate_report`,
    which never raises because *one whole report* falling back to no-LLM
    content is an acceptable, designed-for outcome. Here, one bad image
    failing must not silently pretend to be a valid classification; the
    caller (`classify_patrol`) is what decides a single bad image should be
    skipped rather than aborting the whole patrol's batch.
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    ([{"type": "text", "text": context.as_prompt_text()}] if context else [])
                    + [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "image_classification",
                "schema": _classification_schema(),
                "strict": True,
            },
        },
        timeout=timeout_s,
    )
    return ImageClassification.model_validate_json(response.choices[0].message.content)


async def classify_patrol(
    patrol_id: str,
    source_dir: Path,
    data_root: Path,
    model: str,
    timeout_s: float = 60.0,
    client: AsyncOpenAI | None = None,
    after_ts_ms: int | None = None,
    before_ts_ms: int | None = None,
    concurrency: int | None = None,
    use_ledger: bool = True,
) -> int:
    """Classify every unclassified image in `source_dir`, writing C2-contract
    output under `data_root`, then the `_COMPLETE` marker.

    **Which images.** By default every `*.jpg`/`*.jpeg` in `source_dir` that
    the ledger (`{data_root}/analysis/.classified.json`) has not already
    recorded. This deliberately replaced a filter on the patrol's own
    START/STOP window: on this system the camera runs independently of the
    drive (`web_dashboard/INTEGRATION_RUNBOOK.md` says so outright) and images
    reach the PC only when someone triggers a transfer, so the drive window
    and the capture window routinely do not overlap at all. Scoping by "what
    have we not done yet" matches how the images actually arrive, and the
    ledger is what keeps that affordable — without it every patrol would
    re-classify, and re-bill, the whole day's directory.

    `after_ts_ms`/`before_ts_ms` (both inclusive, both optional) still narrow
    the set by *capture* time — read from the filename by `capture_ts_ms`, not
    from mtime, which records when the file landed on the PC rather than when
    it was taken. They are for manual runs that need to re-examine a specific
    span; the automatic STOP-triggered run passes neither.

    **Idempotence.** `image_id` comes from `image_id_for` (filename-derived),
    so re-running is safe: the same picture always gets the same id, and
    `Store.insert_analysis`'s `INSERT OR IGNORE` therefore updates nothing
    rather than binding a stale row to a rewritten image.

    `client` is injectable so tests never construct a real `AsyncOpenAI`
    (mirrors `ai_report/llm/client.py::generate_report`'s own `client=`
    parameter and its "own_client" pattern exactly, including the same
    construct-inside-try / guard-the-finally fix that bug needed).

    **Always writes `_COMPLETE`** — from a `finally`, so it holds even when
    the run dies before classifying anything (an unset `OPENAI_API_KEY` makes
    `AsyncOpenAI(...)` raise; a vanished day directory makes `iterdir` raise).
    Previously the marker sat after the `try` and those two paths skipped it
    entirely, leaving `ai_report` to block for the whole
    `VIS_COMPLETE_TIMEOUT_S` and then emit an empty report, with the traceback
    visible only in this process's own detached log file. Any pre-existing
    marker is cleared on entry for the same reason in reverse: a stale
    `_COMPLETE` from an earlier run would let `VisWatcher` return before this
    run has written anything.

    Returns the number of images successfully classified. Called by `main`
    and directly by tests.
    """
    own_client = client is None
    images_dir = data_root / "images" / patrol_id
    analysis_dir = data_root / "analysis" / patrol_id
    ledger_path = data_root / "analysis" / LEDGER_NAME
    marker_path = analysis_dir / "_COMPLETE"

    classified = 0
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        analysis_dir.mkdir(parents=True, exist_ok=True)

        # A marker left by an earlier run for this patrol_id would let
        # `VisWatcher.watch` return immediately, before this run writes a
        # single analysis file.
        marker_path.unlink(missing_ok=True)

        sources = sorted(
            (p for p in source_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES),
            key=capture_sort_key,
        )

        # One parse per file, reused for the window filter, the sort above,
        # `FrameContext`'s gap, and the stored `captured_at_ms`.
        capture_times = {p: capture_ts_ms(p) for p in sources}
        check_timezone_alignment(sources, [capture_times[p] for p in sources])

        if after_ts_ms is not None or before_ts_ms is not None:
            sources = [
                p for p in sources
                if (after_ts_ms is None or capture_times[p] >= after_ts_ms)
                and (before_ts_ms is None or capture_times[p] <= before_ts_ms)
            ]

        ledger = _load_ledger(ledger_path) if use_ledger else {}
        if ledger:
            skipped = [p for p in sources if _ledger_key(p) in ledger]
            if skipped:
                logger.info("skipping %d image(s) already classified by a previous run", len(skipped))
            sources = [p for p in sources if _ledger_key(p) not in ledger]

        if not sources:
            logger.warning(
                "no unclassified images found in %s (after_ts_ms=%s, before_ts_ms=%s)",
                source_dir, after_ts_ms, before_ts_ms,
            )

        if own_client:
            client = AsyncOpenAI(api_key=get_settings().OPENAI_API_KEY, timeout=timeout_s)

        if concurrency is None:
            concurrency = get_settings().CLASSIFY_CONCURRENCY
        semaphore = asyncio.Semaphore(max(1, concurrency))
        total = len(sources)
        ordered_times = [capture_times[p] for p in sources]

        async def classify_one(index: int, src: Path) -> str | None:
            """Classify one frame; return its `image_id` on success, else None."""
            image_id = image_id_for(patrol_id, src)
            captured_ms = ordered_times[index]
            previous_ms = ordered_times[index - 1] if index > 0 else None
            context = FrameContext(
                index=index,
                total=total,
                captured_at_ms=captured_ms,
                gap_s=None if previous_ms is None else (captured_ms - previous_ms) / 1000,
            )
            # Both the read and the call are inside the semaphore so that at
            # most `concurrency` images are held in memory at once -- a patrol
            # can be hundreds of frames at 1 capture/sec. Every filesystem
            # call goes through `asyncio.to_thread`: they are blocking, and on
            # the event loop they stall the other workers *and* the shared
            # AsyncOpenAI transport, collapsing the concurrency this function
            # exists to provide.
            async with semaphore:
                image_bytes = await asyncio.to_thread(src.read_bytes)
                try:
                    result = await classify_image(client, image_bytes, model, timeout_s, context)
                except Exception:
                    logger.exception("classification failed for %s; skipping this image", src)
                    return None

                await asyncio.to_thread(shutil.copyfile, src, images_dir / f"{image_id}.jpg")
                analysis = AnalysisResult(
                    image_id=image_id,
                    patrol_id=patrol_id,
                    captured_at_ms=captured_ms,
                    image_path=f"images/{patrol_id}/{image_id}.jpg",
                    image_quality=result.image_quality,
                    detections=result.detections,
                )
                await asyncio.to_thread(
                    _write_analysis_atomically, analysis_dir / f"{image_id}.json", analysis
                )
                logger.info("classified %s -> %d detection(s)", src.name, len(result.detections))
                return image_id

        # return_exceptions=True keeps one unexpected failure (something
        # outside classify_image's own try, e.g. a disk error on write) from
        # cancelling its siblings -- the sequential version skipped and
        # carried on, and `_COMPLETE` must still be written either way.
        outcomes = await asyncio.gather(
            *(classify_one(i, src) for i, src in enumerate(sources)),
            return_exceptions=True,
        )
        for src, outcome in zip(sources, outcomes):
            if isinstance(outcome, BaseException):
                logger.error("unexpected failure writing results for %s: %r", src, outcome)
            elif outcome:
                classified += 1
                ledger[_ledger_key(src)] = {"patrol_id": patrol_id, "image_id": outcome}

        if use_ledger and classified:
            _save_ledger(ledger_path, ledger)
    finally:
        if own_client and client is not None:
            await client.close()
        # In a `finally` so a run that died before classifying anything still
        # releases `ai_report` instead of making it wait out the full timeout.
        try:
            analysis_dir.mkdir(parents=True, exist_ok=True)
            marker_path.touch()
        except OSError as exc:
            logger.error("could not write _COMPLETE marker for patrol_id=%s: %s", patrol_id, exc)

    logger.info(
        "patrol_id=%s: classified %d image(s), wrote _COMPLETE", patrol_id, classified
    )
    return classified


def _write_analysis_atomically(dest: Path, analysis: AnalysisResult) -> None:
    """Serialise one `AnalysisResult` to `dest` via a temp file + `os.replace`.

    `ai_report.ingest.vis_watcher.VisWatcher.scan_once` globs this directory
    roughly once a second while this function is still producing files. A
    plain `write_text` truncates before it writes, so a scan landing inside
    that window reads a partial file; `scan_once` deliberately does not catch
    the resulting `JSONDecodeError`, and `run_patrol_pipeline`'s broad
    `except` turns it into *no report at all*. `os.replace` is atomic on
    POSIX, so a reader sees either the old file or the complete new one --
    the same guarantee, and for the same reason, as
    `ai_report/storage/layout.py::write_report`'s directory swap.

    The temp file is written in `dest`'s own directory so the rename stays on
    one filesystem, and is named with a leading dot so the `*.json` glob never
    picks it up even mid-write.
    """
    tmp = dest.parent / f".{dest.name}.tmp"
    tmp.write_text(
        json.dumps(analysis.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, dest)


def build_arg_parser() -> argparse.ArgumentParser:
    """Define the CLI. Called only by `main`."""
    parser = argparse.ArgumentParser(
        prog="classify", description="Classify captured crop images into VIS's C2 analysis contract"
    )
    parser.add_argument("--patrol-id", required=True)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--data-root", default=None, type=Path, help="defaults to ai_report's configured DATA_ROOT")
    parser.add_argument("--model", default=None, help="defaults to ai_report's configured LLM_MODEL")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--concurrency", type=int, default=None,
                         help="images classified in parallel (default: ai_report's CLASSIFY_CONCURRENCY; 1 = sequential)")
    parser.add_argument("--after-ts-ms", type=int, default=None,
                         help="only classify images captured at or after this epoch-ms value")
    parser.add_argument("--before-ts-ms", type=int, default=None,
                         help="only classify images captured at or before this epoch-ms value")
    parser.add_argument("--reclassify", action="store_true",
                         help="ignore the already-classified ledger and re-run every matching image")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Called by `python -m vision.image_analysis.system.classify ...`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_arg_parser().parse_args(argv)

    if not args.source_dir.is_dir():
        print(f"ERR>> source dir not found: {args.source_dir}", file=sys.stderr)
        return 1

    settings = get_settings()
    data_root = args.data_root or settings.DATA_ROOT
    model = args.model or settings.LLM_MODEL

    classified = asyncio.run(
        classify_patrol(
            args.patrol_id, args.source_dir, data_root, model, args.timeout_s,
            after_ts_ms=args.after_ts_ms, before_ts_ms=args.before_ts_ms,
            concurrency=args.concurrency, use_ledger=not args.reclassify,
        )
    )
    print(f"patrol_id={args.patrol_id} classified {classified} image(s) under {data_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
