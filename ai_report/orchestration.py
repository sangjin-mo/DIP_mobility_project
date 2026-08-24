"""A2 orchestration — the piece `ai_report/CALL_MAP.md` documented as "not
wired up yet": reacting to `PATROL_END` by running the full ①–⑤ chain
(`pipeline.segment.segment_by_crop_type` -> `pipeline.aggregate.aggregate` ->
`pipeline.select_images.apply_image_selection` -> `pipeline.payload.build_payload`
-> `llm.client.generate_report` -> `render.markdown.render_report` ->
`storage.layout.write_report`) for one patrol and writing its report.

Zones are grouped by classified crop type, not by DR's `ZONE_ENTER`
events/telemetry (ADR-0009) — `drive_ver2` never sends either.

GUIDELINES.md: "No scheduling / cron / APScheduler. WEB triggers patrols.
We react to PATROL_END." — this module is the reaction, not a poller. The
caller (`cli.py::_serve`, via `ingest/event_api.py` and
`ingest/udp_listener.py`'s `on_patrol_end` hooks) decides *when* to invoke
`run_patrol_pipeline`; this module only decides *what happens* once invoked.

Called by:
- `cli.py::_serve` — schedules `run_patrol_pipeline` as a background
  `asyncio` task the moment a new (non-duplicate) `PATROL_END` event is
  stored, via the `on_patrol_end` callback threaded through
  `ingest/event_api.py::create_app` and `ingest/udp_listener.py::create_udp_listener`.
- `tests/test_orchestration.py` — calls `run_patrol_pipeline` directly
  against a `Store` populated by `devtools/fake_rover.py` +
  `devtools/fake_vis.py`, with a mocked LLM client (GUIDELINES.md: "No
  network calls in any test").
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai_report.config import Settings
from ai_report.ingest.store import Store
from ai_report.ingest.vis_watcher import VisWatcher
from ai_report.llm.client import generate_report
from ai_report.pipeline.aggregate import aggregate, apply_crop_type_zone_names
from ai_report.pipeline.payload import build_payload, write_payload
from ai_report.pipeline.segment import segment_by_crop_type
from ai_report.pipeline.select_images import (
    apply_image_selection,
    copy_and_resize_images,
    load_selected_images,
)
from ai_report.render.markdown import render_report
from ai_report.storage.layout import write_report

logger = logging.getLogger(__name__)


async def run_patrol_pipeline(
    patrol_id: str,
    store: Store,
    settings: Settings,
    llm_client=None,
) -> Path | None:
    """React to one patrol's `PATROL_END`: wait for VIS, then build and write its report.

    Order of operations, mirroring `CALL_MAP.md`'s "A2-A5 pipeline" diagram
    exactly:

    1. `VisWatcher.watch` — poll for VIS's `_COMPLETE` marker, per spec
       §12's error-handling matrix row ("`_COMPLETE` never written ->
       Timeout 120s -> Proceed with available analyses, note the gap").
       Since ADR-0010 the marker is written by our own classify.py
       subprocess rather than by another team, so this returns as soon as
       classification finishes; the timeout is only the give-up bound.
       `settings.VIS_COMPLETE_TIMEOUT_S`/`VIS_WATCHER_POLL_INTERVAL_S` were
       already declared in `config.py` for exactly this call and were
       unused until now.
    2. Load this patrol's rows back out of `store` (events, analysis —
       whatever VIS wrote before `_COMPLETE` or the timeout; telemetry
       isn't loaded here at all — `segment_by_crop_type` doesn't use it,
       see ADR-0009).
    3. `segment_by_crop_type` -> `aggregate` -> `apply_crop_type_zone_names`
       -> `apply_image_selection` -> `build_payload` -> `generate_report` ->
       `render_report` -> `write_report`. Mirrors the chain
       `cli.py::_regenerate` (A6) already proves works end-to-end, except
       regenerate reloads already-resized images from a prior report's
       `images/` directory while this, the *first* build for a patrol,
       resizes them fresh via `load_selected_images` (in-memory, for the
       LLM call) and `copy_and_resize_images` (to disk, via
       `write_report`'s `extra_writers` — see that function's own
       docstring and the `[!FLAG]` in `storage/layout.py` for why these
       must go through `extra_writers` rather than being written to the
       final path directly).

    `llm_client` is forwarded to `generate_report` unchanged (`None` in
    production, constructing a real `AsyncOpenAI`; tests inject a mock —
    the same pattern `generate_report` itself documents).

    Never raises: this runs as a fire-and-forget background task
    (`cli.py::_serve`'s `on_patrol_end` callback), so nothing here may crash
    the long-running `serve` process over one bad patrol. Every stage this
    function calls already has its own fallback per spec §12 *except*
    `ingest/vis_watcher.py::VisWatcher.scan_once`, which the ICD requires to
    raise on a contract violation (an unknown VIS `state` enum value) rather
    than silently drop it — so that one exception, and any other genuinely
    unexpected failure, is caught here, logged with `patrol_id`, and turned
    into a `None` return instead of an unhandled task exception. Returns the
    final report directory on success, `None` on failure.
    """
    try:
        watcher = VisWatcher(store, settings.DATA_ROOT, settings.VIS_WATCHER_POLL_INTERVAL_S)
        scan = await watcher.watch(patrol_id, timeout_s=settings.VIS_COMPLETE_TIMEOUT_S)
        if not scan.complete:
            logger.warning(
                "VIS _COMPLETE not observed for patrol_id=%s within %ss; "
                "proceeding with %d analysis record(s) received so far",
                patrol_id, settings.VIS_COMPLETE_TIMEOUT_S, scan.new_records,
            )

        events = store.events_for_patrol(patrol_id)
        analysis = store.analysis_for_patrol(patrol_id)

        # ADR-0009: zones grouped by classified crop type, not by
        # ZONE_ENTER/distance -- drive_ver2 never sends either, and the
        # deployed rover has no physical zone concept to segment by at all.
        segmentation = segment_by_crop_type(patrol_id, events, analysis)

        udp_received = len(store.received_telemetry_seqs(patrol_id))
        max_seq = store.max_telemetry_seq(patrol_id)
        udp_expected = (max_seq + 1) if max_seq is not None else 0

        agg = aggregate(segmentation, udp_received, udp_expected, settings)
        agg = apply_crop_type_zone_names(agg, segmentation, settings)
        agg = apply_image_selection(agg, segmentation, settings)
        payload, _tokens = build_payload(agg, segmentation, settings)

        images = load_selected_images(agg, segmentation, settings.DATA_ROOT, settings)
        valid_zone_ids = {z.zone_id for z in agg.zones}
        llm_output, llm_metadata = await generate_report(
            payload, images, valid_zone_ids, settings, client=llm_client
        )
        agg = agg.model_copy(update={"llm": llm_metadata})

        md = render_report(
            agg,
            segmentation.obstruction_counts(),
            llm=llm_output,
            coverage_warn_threshold=settings.COVERAGE_WARN_THRESHOLD,
        )

        report_dir = write_report(
            patrol_id, md, agg, settings.REPORT_ROOT,
            extra_writers=[
                lambda tmp: copy_and_resize_images(agg, segmentation, settings.DATA_ROOT, tmp, settings),
                lambda tmp: write_payload(payload, tmp),
            ],
        )
        logger.info("patrol pipeline complete for patrol_id=%s at %s", patrol_id, report_dir)
        return report_dir
    except Exception:
        logger.exception("patrol pipeline failed for patrol_id=%s", patrol_id)
        return None
