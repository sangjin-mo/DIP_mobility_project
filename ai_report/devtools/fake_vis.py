"""VIS emitter stand-in — writes synthetic C2 analysis JSON plus the
`_COMPLETE` marker, so `ingest/vis_watcher.py` can be exercised without a
real YOLO pipeline. See CLAUDE.md's ownership boundary: this fakes VIS's
*output*, it does not run any detection model.

Call flow for `python -m ai_report.devtools.fake_vis` (`main`):
  main
   |- build_arg_parser              (parse CLI flags)
   |- get_settings                  (config.py, for default data root)
   |- generate_analysis_results     (build fake AnalysisResult objects; pure)
   `- write_analysis_files          (serialise them to data/analysis/{patrol_id}/*.json + _COMPLETE)

The receiving side of those files is `ingest/vis_watcher.py::VisWatcher.scan_once`.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from ai_report.config import get_settings
from ai_report.models import AnalysisResult, CropState, Detection

logger = logging.getLogger(__name__)

_STATE_WEIGHTS = {
    CropState.NORMAL: 6,
    CropState.IMMATURE: 2,
    CropState.SUSPECTED_DISEASE: 1,
    CropState.UNDETERMINED: 1,
}


@dataclass
class VisPlan:
    """Currently unused by any function below (results are passed around as a
    plain `list[AnalysisResult]` instead); kept as the natural pairing type
    for `patrol_id` + its results, mirroring `fake_rover.py`'s `PatrolPlan`.
    """

    patrol_id: str
    results: list[AnalysisResult]


def generate_analysis_results(
    patrol_id: str,
    num_zones: int = 6,
    images_per_zone: int = 5,
    duration_s: int = 1200,
    seed: int | None = 0,
) -> list[AnalysisResult]:
    """Pure generator — no filesystem I/O.

    Deterministic for a fixed `seed`. For each of `num_zones` zones,
    generates `images_per_zone` `AnalysisResult`s with:
    - `image_id` = `"{patrol_id}_z{zone}_{k:03d}"`, `image_path` pointing at
      a matching (not-yet-created) placeholder JPEG path.
    - `captured_at_ms` spread evenly across `duration_s`, in zone order —
      mirrors how `fake_rover.py::generate_patrol_plan` spreads its
      telemetry, so a `fake_rover` + `fake_vis` run against the same
      `patrol_id`/`duration_s` produce timestamp-consistent fake data.
    - `image_quality` uniform in [0.3, 0.95] (spans both sides of the 0.40
      selection floor from spec §7, on purpose).
    - 0-3 `Detection`s per image, states drawn from `CropState` with weights
      favouring `정상` (`_STATE_WEIGHTS`), and `confidence=None` about half
      the time when the drawn state is `판단불가` (confidence is only
      allowed to be null for that state — see `models.py::Detection`).

    Called by `main` (CLI use) and directly by `tests/test_fake_vis.py`.
    """
    rng = random.Random(seed)
    states = list(_STATE_WEIGHTS.keys())
    weights = list(_STATE_WEIGHTS.values())

    results: list[AnalysisResult] = []
    for zone in range(1, num_zones + 1):
        for k in range(images_per_zone):
            image_id = f"{patrol_id}_z{zone}_{k:03d}"
            quality = round(rng.uniform(0.3, 0.95), 2)
            captured_at_ms = int(
                ((zone - 1) + (k + 1) / (images_per_zone + 1)) / num_zones * duration_s * 1000
            )

            detections: list[Detection] = []
            for _ in range(rng.randint(0, 3)):
                state = rng.choices(states, weights=weights, k=1)[0]
                confidence = (
                    None
                    if state == CropState.UNDETERMINED and rng.random() < 0.5
                    else round(rng.uniform(0.5, 0.95), 2)
                )
                detections.append(
                    Detection.model_validate(
                        {
                            "class": "tomato",
                            "state": state,
                            "count": rng.randint(1, 5),
                            "confidence": confidence,
                        }
                    )
                )

            results.append(
                AnalysisResult(
                    image_id=image_id,
                    patrol_id=patrol_id,
                    captured_at_ms=captured_at_ms,
                    image_path=f"images/{patrol_id}/z{zone}_{k:03d}.jpg",
                    image_quality=quality,
                    detections=detections,
                )
            )
    return results


def write_analysis_files(
    results: list[AnalysisResult],
    data_root: Path,
    patrol_id: str,
    write_complete: bool = True,
    write_placeholder_images: bool = True,
) -> None:
    """Serialise `results` to `data_root/analysis/{patrol_id}/{image_id}.json`.

    One file per `AnalysisResult`, JSON-dumped with `by_alias=True` so the
    `class_` field round-trips as the contract's `"class"` key. When
    `write_placeholder_images` is set, also touches an empty file at each
    result's `image_path` under `data_root` (real image bytes aren't needed
    for A1's ingest path, but the referenced path existing keeps the
    directory layout realistic). When `write_complete` is set, finally
    touches `_COMPLETE` in the analysis directory — this is the signal
    `VisWatcher.scan_once`/`.watch` polls for.

    Called by `main` (CLI use) and directly by `tests/test_fake_vis.py`,
    which also exercises `write_complete=False` to test the not-yet-complete path.
    """
    analysis_dir = Path(data_root) / "analysis" / patrol_id
    analysis_dir.mkdir(parents=True, exist_ok=True)

    for result in results:
        out_path = analysis_dir / f"{result.image_id}.json"
        out_path.write_text(
            json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if write_placeholder_images:
            image_path = Path(data_root) / result.image_path
            image_path.parent.mkdir(parents=True, exist_ok=True)
            if not image_path.exists():
                image_path.write_bytes(b"")

    if write_complete:
        (analysis_dir / "_COMPLETE").touch()


def build_arg_parser() -> argparse.ArgumentParser:
    """Define the `fake_vis` command-line interface. Called only by `main`."""
    parser = argparse.ArgumentParser(prog="fake_vis", description="Emit synthetic VIS analysis output")
    parser.add_argument("--patrol-id", required=True)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--zones", type=int, default=6)
    parser.add_argument("--images-per-zone", type=int, default=5)
    parser.add_argument("--duration-s", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-complete", action="store_true", help="omit the _COMPLETE marker")
    parser.add_argument("--no-images", action="store_true", help="skip placeholder image files")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, generate fake results, write them to disk.

    Called by `python -m ai_report.devtools.fake_vis ...` (the `if __name__`
    guard below) and, indirectly, by anyone running the devtool from a shell.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_arg_parser().parse_args(argv)
    settings = get_settings()
    data_root = Path(args.data_root) if args.data_root else settings.DATA_ROOT

    results = generate_analysis_results(
        args.patrol_id,
        num_zones=args.zones,
        images_per_zone=args.images_per_zone,
        duration_s=args.duration_s,
        seed=args.seed,
    )
    write_analysis_files(
        results,
        data_root,
        args.patrol_id,
        write_complete=not args.no_complete,
        write_placeholder_images=not args.no_images,
    )
    logger.info(
        "patrol_id=%s wrote %d analysis files to %s (complete=%s)",
        args.patrol_id,
        len(results),
        data_root / "analysis" / args.patrol_id,
        not args.no_complete,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
