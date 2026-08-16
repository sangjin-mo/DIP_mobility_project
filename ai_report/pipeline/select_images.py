"""③ Image selection — spec §7. Chooses at most `IMAGES_PER_ZONE_MAX`
images per zone in a fixed priority order, then resizes and copies the
selected files into the report directory.

Priority order per zone (stops as soon as `IMAGES_PER_ZONE_MAX` is reached):

1. **Anomaly exemplar** — among images containing a `병충해_의심` detection,
   the highest `image_quality`.
2. **Normal representative** — among remaining images (not already claimed
   by step 1), the one whose `정상` count is nearest the median `정상`
   count of that same remaining pool, highest quality breaking ties. The
   anomaly exemplar is excluded from the median on purpose — see
   `select_images_for_zone`'s inline comment.
3. **Undetermined exemplar** — only when the zone's `undetermined_rate`
   exceeds `UNDETERMINED_FLAG_THRESHOLD`, the highest-quality remaining
   image containing a `판단불가` detection.

A hard quality filter (`image_quality >= IMAGE_QUALITY_MIN`) runs before
any of the above — an image below the floor is never a candidate for any
slot, full stop.

Called by: whatever runs the pipeline for a patrol — currently only
`tests/test_select_images.py`; production orchestration (on `PATROL_END` +
VIS `_COMPLETE`) is a later phase's addition. `select_images_for_zone` and
`apply_image_selection` are pure (no I/O); `copy_and_resize_images` writes
resized copies to disk, and `load_selected_images` (used by
`llm/client.py::generate_report`) reads and resizes the same images into
memory instead — see that function's docstring for why these are two
separate entry points rather than one.
"""

from __future__ import annotations

import io
import logging
import statistics
from pathlib import Path

from PIL import Image

from ai_report.config import Settings
from ai_report.models import AnalysisResult, CropState, PatrolAggregate
from ai_report.pipeline.segment import PatrolSegmentation, ZoneWindow

logger = logging.getLogger(__name__)


def _count_state(result: AnalysisResult, state: CropState) -> int:
    """Total count of detections matching `state` within one image."""
    return sum(d.count for d in result.detections if d.state == state)


def _has_state(result: AnalysisResult, state: CropState) -> bool:
    """Whether any detection in this image matches `state`."""
    return any(d.state == state for d in result.detections)


def select_images_for_zone(
    window: ZoneWindow | None,
    undetermined_rate: float | None,
    settings: Settings,
) -> list[str]:
    """Pick up to `settings.IMAGES_PER_ZONE_MAX` image_ids for one zone, in priority order.

    `window` is `None` when a zone in `PatrolAggregate.zones` has no
    matching `ZoneWindow` in the segmentation passed to
    `apply_image_selection` (shouldn't happen in practice — both come from
    the same segmentation — but handled rather than crashing on a KeyError-
    shaped bug). Returns `[]` immediately if there's no window or no image
    passes the quality floor, matching spec §7: "If a zone has no eligible
    images, it contributes text-only."

    Pure — no filesystem access. Called by `apply_image_selection`, once
    per zone, and directly by `tests/test_select_images.py`.
    """
    if window is None:
        return []

    eligible = [a for a in window.analysis if a.image_quality >= settings.IMAGE_QUALITY_MIN]
    if not eligible:
        return []

    selected: list[AnalysisResult] = []
    selected_ids: set[str] = set()
    max_images = settings.IMAGES_PER_ZONE_MAX

    # 1. Anomaly exemplar
    if len(selected) < max_images:
        candidates = [a for a in eligible if _has_state(a, CropState.SUSPECTED_DISEASE)]
        if candidates:
            best = max(candidates, key=lambda a: a.image_quality)
            selected.append(best)
            selected_ids.add(best.image_id)

    # 2. Normal representative — nearest the median 정상 count among the
    # remaining candidates, highest quality as tiebreak. The median is
    # computed over `remaining` (eligible images not already claimed by
    # step 1), not all eligible images: spec §7 says "nearest the zone
    # median" without saying which population that's a median *of*, and
    # including the anomaly exemplar would pull the median toward its own
    # 정상 count (often 0, since that image was selected specifically for
    # containing 병충해_의심) — skewing "typical" for a zone where the
    # anomaly image isn't representative of anything but itself.
    if len(selected) < max_images:
        remaining = [a for a in eligible if a.image_id not in selected_ids]
        if remaining:
            median_normal = statistics.median(_count_state(a, CropState.NORMAL) for a in remaining)
            best = min(
                remaining,
                key=lambda a: (abs(_count_state(a, CropState.NORMAL) - median_normal), -a.image_quality),
            )
            selected.append(best)
            selected_ids.add(best.image_id)

    # 3. Undetermined exemplar — only when the zone is already flagged for it.
    if len(selected) < max_images and undetermined_rate is not None and undetermined_rate > settings.UNDETERMINED_FLAG_THRESHOLD:
        remaining = [a for a in eligible if a.image_id not in selected_ids]
        candidates = [a for a in remaining if _has_state(a, CropState.UNDETERMINED)]
        if candidates:
            best = max(candidates, key=lambda a: a.image_quality)
            selected.append(best)
            selected_ids.add(best.image_id)

    return [a.image_id for a in selected[:max_images]]


def apply_image_selection(
    agg: PatrolAggregate, segmentation: PatrolSegmentation, settings: Settings
) -> PatrolAggregate:
    """Return a new `PatrolAggregate` with every zone's `image_ids` populated.

    `aggregate()` (A2) always leaves `image_ids` as `[]`, since selection
    needs `undetermined_rate` — which `aggregate()` itself computes — as an
    input (spec §7's third priority tier only applies when a zone is
    already over the undetermined threshold). This is why selection is a
    separate pass over an already-built `PatrolAggregate` rather than
    something `aggregate()` could do itself.

    Does not mutate `agg` — builds a new `ZoneMetadata` per zone via
    `model_copy` and a new `PatrolAggregate` wrapping them, consistent with
    `pipeline/aggregate.py`'s treatment of `PatrolAggregate` as an
    immutable value once built. Called by whatever runs the pipeline for a
    patrol; directly by `tests/test_select_images.py`.
    """
    windows_by_zone = {w.zone_id: w for w in segmentation.zones()}
    new_zones = []
    for zone in agg.zones:
        image_ids = select_images_for_zone(windows_by_zone.get(zone.zone_id), zone.undetermined_rate, settings)
        new_zones.append(zone.model_copy(update={"image_ids": image_ids}))
    return agg.model_copy(update={"zones": new_zones})


def _resize_image_bytes(src: Path, settings: Settings) -> bytes | None:
    """Resize one image file to `IMAGE_RESIZE_PX` on the long edge, returning JPEG bytes.

    Returns `None` (rather than raising) on a missing or undecodable
    source file — CLAUDE.md's "never fabricate data on missing input"
    extends to not letting one bad file abort an entire report. Shared by
    `copy_and_resize_images` (writes the bytes to disk) and
    `load_selected_images` (keeps them in memory for the LLM call) so the
    resize logic — and its `IMAGE_RESIZE_PX`/`IMAGE_JPEG_QUALITY` config —
    exists in exactly one place.
    """
    if not src.is_file():
        logger.warning("image source file missing at %s; skipping", src)
        return None
    try:
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail((settings.IMAGE_RESIZE_PX, settings.IMAGE_RESIZE_PX))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=settings.IMAGE_JPEG_QUALITY)
            return buf.getvalue()
    except OSError:
        logger.warning("image at %s could not be read/resized; skipping", src)
        return None


def _selected_image_sources(
    agg: PatrolAggregate, segmentation: PatrolSegmentation, data_root: Path
) -> list[tuple[str, Path]]:
    """`(image_id, source_path)` for every selected image across every zone, in zone order.

    Called by both `copy_and_resize_images` and `load_selected_images` so
    the "which images, from where" logic — looking `image_id` up against
    every zone's analysis rows to find its `image_path` — isn't duplicated.
    """
    image_id_to_result = {a.image_id: a for window in segmentation.zones() for a in window.analysis}
    sources: list[tuple[str, Path]] = []
    for zone in agg.zones:
        for image_id in zone.image_ids:
            result = image_id_to_result.get(image_id)
            if result is None:
                logger.warning("selected image_id=%s has no matching analysis row; skipping", image_id)
                continue
            sources.append((image_id, Path(data_root) / result.image_path))
    return sources


def copy_and_resize_images(
    agg: PatrolAggregate, segmentation: PatrolSegmentation, data_root: Path, dest_dir: Path, settings: Settings
) -> list[str]:
    """Resize every selected image to `IMAGE_RESIZE_PX` on the long edge and copy it into `dest_dir`.

    `dest_dir` must be the temp directory `storage/layout.py::write_report`
    is about to atomically swap into place — pass this function via that
    function's `extra_writers`, e.g.
    `write_report(..., extra_writers=[lambda tmp: copy_and_resize_images(agg, seg, data_root, tmp, settings)])`.
    Writing to the *final* report path directly and calling `write_report`
    afterward loses this directory: `write_report`'s atomic swap builds a
    fresh temp directory and renames it over the final path, discarding
    anything written to the final path outside that swap — a real bug an
    end-to-end smoke test caught during development (see the `[!FLAG]` in
    `storage/layout.py`).

    Output files are named `{image_id}.jpg` regardless of the source
    extension, matching ICD §C3.1's `images/z3_007.jpg` convention.

    Returns the list of image_ids actually copied (a subset of every
    zone's `image_ids` if any source file was missing or unreadable — see
    `_resize_image_bytes`).
    """
    images_dir = Path(dest_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for image_id, src in _selected_image_sources(agg, segmentation, data_root):
        data = _resize_image_bytes(src, settings)
        if data is None:
            continue
        (images_dir / f"{image_id}.jpg").write_bytes(data)
        copied.append(image_id)

    return copied


def load_selected_images(
    agg: PatrolAggregate, segmentation: PatrolSegmentation, data_root: Path, settings: Settings
) -> dict[str, bytes]:
    """Resize every selected image and return `{image_id: jpeg_bytes}`, in memory.

    Used by `llm/client.py::generate_report` to build the vision message
    content for the API call. Deliberately independent of
    `copy_and_resize_images`'s `dest_dir`/atomic-swap timing (see that
    function's docstring) — the LLM call must run *before*
    `storage/layout.py::write_report` (its output feeds the render step),
    while `copy_and_resize_images` runs *as one of `write_report`'s*
    `extra_writers`. Both ultimately call the same `_resize_image_bytes`,
    so an image is resized identically whichever path uses it; the API
    call and the archived copy in the report directory just aren't
    guaranteed to be produced from the same resize invocation.

    An image whose source is missing or unreadable is omitted from the
    result (not raised) — same reasoning as `copy_and_resize_images`.
    """
    images: dict[str, bytes] = {}
    for image_id, src in _selected_image_sources(agg, segmentation, data_root):
        data = _resize_image_bytes(src, settings)
        if data is not None:
            images[image_id] = data
    return images
