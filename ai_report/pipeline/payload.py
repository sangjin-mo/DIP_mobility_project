"""④ Payload construction — spec §8. Builds the exact object A5 will send
to the LLM, estimating its token cost first and degrading image coverage
uniformly across every zone if the estimate is over `LLM_MAX_INPUT_TOKENS`.

Token estimate (spec §8's table, none of it real tokenization — no
tokenizer dependency exists in this package):

    TOKEN_ESTIMATE_SYSTEM_PROMPT + TOKEN_ESTIMATE_FIXED
    + (zone count × TOKEN_ESTIMATE_PER_ZONE)
    + (image count × TOKEN_ESTIMATE_PER_IMAGE)

Over-budget behaviour degrades in the order spec §8 specifies: try
`IMAGES_PER_ZONE_MAX` images per zone, then 2, then 1, then 0 (text-only),
stopping at the first budget that fits — text-only is always accepted
regardless of estimate, since spec's ladder has nowhere further to go.
Each degradation step below the zone's own selected count is recorded in
`Payload.known_limitations`, not silently applied.

`load_payload`/`payload_to_aggregate` are the read-side counterpart, added
for A6: `cli.py regenerate {patrol_id}` has no rover or database access
(spec §11), so it reconstructs everything `render_report` needs — a
`PatrolAggregate` and the `obstructions` dict — from a previously-written
`payload.json` alone, rather than re-running ①/②/③.

Called by: whatever runs the pipeline for a patrol — currently only
`tests/test_payload.py`; production orchestration (on `PATROL_END` + VIS
`_COMPLETE`, immediately before the LLM call) is a later phase's addition.
`build_payload`/`payload_to_aggregate` are pure (no I/O); `write_payload`/
`load_payload` are the only functions here that touch the filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_report.config import Settings
from ai_report.models import LlmMetadata, PatrolAggregate, Payload, ZoneMetadata
from ai_report.pipeline.segment import PatrolSegmentation

# The degradation ladder itself (3 -> 2 -> 1 -> 0) isn't config — it's the
# fixed sequence spec §8 describes, not a tunable threshold. Only the
# starting point (settings.IMAGES_PER_ZONE_MAX) is configurable.
_DEGRADED_IMAGE_COUNTS = (2, 1, 0)


def estimate_tokens(num_zones: int, total_images: int, settings: Settings) -> int:
    """Spec §8's token-count heuristic. Called by `build_payload` once per
    candidate image budget while searching for one that fits.
    """
    return (
        settings.TOKEN_ESTIMATE_SYSTEM_PROMPT
        + settings.TOKEN_ESTIMATE_FIXED
        + num_zones * settings.TOKEN_ESTIMATE_PER_ZONE
        + total_images * settings.TOKEN_ESTIMATE_PER_IMAGE
    )


def _truncate_images(zone: ZoneMetadata, max_images: int) -> ZoneMetadata:
    """A copy of `zone` with `image_ids` cut to its first `max_images` entries.

    Safe to call with `max_images >= len(zone.image_ids)` (a no-op slice).
    Keeps the highest-priority images per spec §7's ordering, since
    `select_images_for_zone` already returned `image_ids` in priority
    order — "drop to 2 images per zone" means keep the best 2, not pick
    a different 2.
    """
    return zone.model_copy(update={"image_ids": zone.image_ids[:max_images]})


def build_payload(
    agg: PatrolAggregate, segmentation: PatrolSegmentation, settings: Settings
) -> tuple[Payload, int]:
    """Build the LLM-input `Payload`, degrading image coverage until it fits the token budget.

    `agg.zones` is expected to already have `image_ids` populated by
    `pipeline/select_images.py::apply_image_selection` — this function
    only ever *removes* images (via `_truncate_images`), it never adds or
    re-selects any.

    Tries `settings.IMAGES_PER_ZONE_MAX` images per zone first (i.e.
    whatever `agg` already has), then walks `_DEGRADED_IMAGE_COUNTS`
    (2, 1, 0), stopping at the first budget whose `estimate_tokens(...)` is
    `<= settings.LLM_MAX_INPUT_TOKENS`. Text-only (0 images) is always
    accepted as the final fallback even if its own estimate is still over
    budget — spec §8's ladder has no step past that.

    Returns `(payload, estimated_tokens)` for the budget that was
    ultimately used. Called by whatever runs the pipeline for a patrol,
    immediately before A5's LLM call; directly by `tests/test_payload.py`.
    """
    obstructions = segmentation.obstruction_counts()
    known_limitations: list[str] = []

    candidate_budgets = (settings.IMAGES_PER_ZONE_MAX, *_DEGRADED_IMAGE_COUNTS)
    zones = agg.zones
    tokens = estimate_tokens(len(zones), sum(len(z.image_ids) for z in zones), settings)

    for max_images in candidate_budgets:
        zones = [_truncate_images(z, max_images) for z in agg.zones]
        total_images = sum(len(z.image_ids) for z in zones)
        tokens = estimate_tokens(len(zones), total_images, settings)

        fits = tokens <= settings.LLM_MAX_INPUT_TOKENS
        is_text_only = max_images == 0
        if fits or is_text_only:
            if max_images < settings.IMAGES_PER_ZONE_MAX:
                if is_text_only:
                    known_limitations.append(
                        "토큰 예산 초과로 이미지 없이 텍스트 정보만으로 리포트를 생성했습니다."
                    )
                else:
                    known_limitations.append(
                        f"토큰 예산 초과로 구역당 이미지 수를 최대 {max_images}장으로 줄였습니다."
                    )
            break

    payload = Payload(
        patrol_id=agg.patrol_id,
        patrol_date=agg.patrol_date,
        duration_min=agg.duration_min,
        overall_status=agg.overall_status,
        data_completeness=agg.data_completeness,
        zones=zones,
        obstructions=obstructions,
        known_limitations=known_limitations,
        prompt_version=settings.PROMPT_VERSION,
    )
    return payload, tokens


def write_payload(payload: Payload, dest_dir: Path) -> Path:
    """Write `payload.json` into `dest_dir`.

    Like `pipeline/select_images.py::copy_and_resize_images`, `dest_dir`
    must be the temp directory `storage/layout.py::write_report` is about
    to atomically swap into place — pass this via that function's
    `extra_writers`, e.g. `write_report(..., extra_writers=[lambda tmp:
    write_payload(payload, tmp)])`. Writing to the final report path
    directly and calling `write_report` afterward loses the file — see the
    `[!FLAG]` in `storage/layout.py` for why.

    Called by whatever runs the pipeline for a patrol; directly by
    `tests/test_payload.py`.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / "payload.json"
    out_path.write_text(
        json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_path


def load_payload(path: Path) -> Payload:
    """Read and parse a stored `payload.json` back into a `Payload`.

    The read side of `write_payload`. Called by `cli.py`'s `regenerate`
    command — the one place in this codebase that reconstructs pipeline
    state from disk instead of running the pipeline forward.
    """
    return Payload.model_validate_json(Path(path).read_text(encoding="utf-8"))


def payload_to_aggregate(payload: Payload) -> PatrolAggregate:
    """Reconstruct a `PatrolAggregate` from a stored `Payload`, for regeneration.

    A straight field copy, not a re-derivation: `Payload`'s fields are
    deliberately `PatrolAggregate`'s shape minus `llm` (see `Payload`'s own
    docstring in `models.py`) plus `obstructions`/`known_limitations`,
    which neither `PatrolAggregate` nor `metadata.json` carry. `llm` is set
    to the same `LlmMetadata(enabled=False)` placeholder `aggregate()`
    itself produces — the real value is merged in after `generate_report()`
    runs again, exactly like the first time this patrol's report was built.

    Called by `cli.py`'s `regenerate` command.
    """
    return PatrolAggregate(
        patrol_id=payload.patrol_id,
        patrol_date=payload.patrol_date,
        duration_min=payload.duration_min,
        overall_status=payload.overall_status,
        llm=LlmMetadata(enabled=False),
        data_completeness=payload.data_completeness,
        zones=payload.zones,
    )
