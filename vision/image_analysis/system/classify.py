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

Produces, for every `*.jpg`/`*.jpeg` in `--source-dir`:
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
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import shutil
import sys
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from ai_report.config import get_settings
from ai_report.llm.schema import _make_strict
from ai_report.models import AnalysisResult, Detection

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".jpg", ".jpeg")

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


def _classification_schema() -> dict:
    """Strict JSON schema for the API's `response_format`, reusing the same
    `_make_strict` helper `ai_report/llm/schema.py::output_json_schema`
    uses — that function is generic (any Pydantic-generated schema dict in,
    same dict out), so there is nothing AI-report-specific about importing
    it here. Called by `classify_image`.
    """
    return _make_strict(ImageClassification.model_json_schema(by_alias=True))


async def classify_image(
    client: AsyncOpenAI, image_bytes: bytes, model: str, timeout_s: float
) -> ImageClassification:
    """One vision LLM call for one image's raw bytes.

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
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
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
) -> int:
    """Classify every image in `source_dir`, writing C2-contract output
    under `data_root`, then the `_COMPLETE` marker.

    `image_id` is `{patrol_id}_{index:03d}` in filename-sorted order —
    zone assignment doesn't happen here (that's
    `pipeline/segment.py::segment_patrol`'s job, driven by
    `captured_at_ms`, not by anything encoded in `image_id`), so no zone
    label is needed at classification time.

    `client` is injectable so tests never construct a real `AsyncOpenAI`
    (mirrors `ai_report/llm/client.py::generate_report`'s own `client=`
    parameter and its "own_client" pattern exactly, including the same
    construct-inside-try / guard-the-finally fix that bug needed).

    Always writes `_COMPLETE`, even if every image failed to classify —
    `ai_report`'s spec §12 error matrix already handles "`_COMPLETE` never
    written" as a 600s-timeout fallback, but there is no reason to make
    `ai_report` wait out that timeout when this script already knows it is
    done trying. A patrol with zero successfully classified images still
    produces a text-only report on the `ai_report` side, same as any other
    zero-detections case.

    `after_ts_ms`/`before_ts_ms` (both inclusive, both optional) restrict
    `source_dir` to files whose mtime falls in that window -- the same
    epoch-ms clock `web_dashboard/services/patrol_event_service.py` stamps
    its `PATROL_START`/`PATROL_END` events with. This is what lets a single
    shared `received/{date}/` directory (holding every patrol's images for
    that day) be classified one patrol at a time: `web_dashboard`'s STOP
    handler passes this patrol's own start/end timestamps so images from a
    different patrol earlier or later the same day are left alone. mtime
    (not the capture-time embedded in the filename) is used deliberately --
    it is already `captured_at_ms`'s own source of truth below, and using
    the same field for both the filter and the stored value keeps them from
    disagreeing with each other even by a few seconds.

    Returns the number of images successfully classified. Called by `main`
    and directly by tests.
    """
    own_client = client is None
    images_dir = data_root / "images" / patrol_id
    analysis_dir = data_root / "analysis" / patrol_id
    images_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    sources = sorted(p for p in source_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if after_ts_ms is not None or before_ts_ms is not None:
        sources = [
            p for p in sources
            if (after_ts_ms is None or p.stat().st_mtime * 1000 >= after_ts_ms)
            and (before_ts_ms is None or p.stat().st_mtime * 1000 <= before_ts_ms)
        ]
    if not sources:
        logger.warning("no images found in %s (after_ts_ms=%s, before_ts_ms=%s)",
                        source_dir, after_ts_ms, before_ts_ms)

    classified = 0
    try:
        if own_client:
            client = AsyncOpenAI(api_key=get_settings().OPENAI_API_KEY, timeout=timeout_s)

        for index, src in enumerate(sources):
            image_id = f"{patrol_id}_{index:03d}"
            image_bytes = src.read_bytes()

            try:
                result = await classify_image(client, image_bytes, model, timeout_s)
            except Exception:
                logger.exception("classification failed for %s; skipping this image", src)
                continue

            shutil.copyfile(src, images_dir / f"{image_id}.jpg")
            analysis = AnalysisResult(
                image_id=image_id,
                patrol_id=patrol_id,
                captured_at_ms=int(src.stat().st_mtime * 1000),
                image_path=f"images/{patrol_id}/{image_id}.jpg",
                image_quality=result.image_quality,
                detections=result.detections,
            )
            (analysis_dir / f"{image_id}.json").write_text(
                json.dumps(analysis.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            classified += 1
            logger.info("classified %s -> %d detection(s)", src.name, len(result.detections))
    finally:
        if own_client and client is not None:
            await client.close()

    (analysis_dir / "_COMPLETE").touch()
    logger.info(
        "patrol_id=%s: classified %d/%d image(s), wrote _COMPLETE",
        patrol_id, classified, len(sources),
    )
    return classified


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
    parser.add_argument("--after-ts-ms", type=int, default=None,
                         help="only classify images with mtime >= this epoch-ms value")
    parser.add_argument("--before-ts-ms", type=int, default=None,
                         help="only classify images with mtime <= this epoch-ms value")
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
        )
    )
    print(f"patrol_id={args.patrol_id} classified {classified} image(s) under {data_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
