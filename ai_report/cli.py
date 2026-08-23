"""Entry points. This is the only module allowed to use `print`.

Called by: the `ai-report` console script (registered in `pyproject.toml`),
or directly via `python -m ai_report.cli serve` / `python -m ai_report.cli
regenerate {patrol_id}`. `serve` wires up the whole A1 ingest path in one
process (`get_settings`, `Store`, `create_udp_listener`, `create_app`).
`regenerate` (A6) rebuilds one report from its own stored `payload.json` —
see `_regenerate`'s docstring for why that's the one command in this
codebase with no rover or database dependency at all.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import uvicorn

from ai_report.config import Settings, get_settings
from ai_report.ingest.event_api import create_app
from ai_report.ingest.store import Store
from ai_report.ingest.udp_listener import create_udp_listener
from ai_report.llm.client import generate_report
from ai_report.models import Payload
from ai_report.orchestration import run_patrol_pipeline
from ai_report.pipeline.payload import load_payload, payload_to_aggregate, write_payload
from ai_report.render.markdown import render_report
from ai_report.storage.layout import write_report

logger = logging.getLogger(__name__)


def _make_patrol_end_trigger(store: Store, settings: Settings) -> tuple[Callable[[str], None], set]:
    """Build the `on_patrol_end` callback `_serve` passes to both ingest paths.

    Returns `(trigger, background_tasks)`. `trigger(patrol_id)` schedules
    `orchestration.py::run_patrol_pipeline` as a fire-and-forget
    `asyncio.Task` — GUIDELINES.md: "We react to PATROL_END", and A1's
    ingest handlers (`event_api.py::post_event`, `udp_listener.py::_handle`)
    must return immediately, not block on a report that can take minutes
    (LLM retries included).

    `background_tasks` is a plain `set` the caller must keep alive for the
    life of the server: asyncio only holds a *weak* reference to a task
    created via `asyncio.create_task` and not otherwise referenced, so an
    unreferenced task can be garbage-collected mid-run — a well-known
    asyncio pitfall. `trigger` adds each task to this set and removes it via
    `add_done_callback` once finished, so the set's steady-state size is the
    number of patrols currently being processed, not ever-growing.

    A `patrol_id` already scheduled (its `PATROL_END` re-delivered, e.g. a
    UDP fallback repeat racing the HTTP primary) is not scheduled twice —
    `run_patrol_pipeline` makes one real LLM call per invocation, and
    silently double-spending that isn't something `Store.insert_event`'s
    dedup alone prevents, since it dedups by `(patrol_id, event_seq)`, not
    by `patrol_id` alone, and DR is free to reuse the same `PATROL_END`
    `event_seq` on both channels or not — the ICD doesn't guarantee either
    way. Called by `_serve`.
    """
    background_tasks: set = set()
    triggered_patrol_ids: set[str] = set()

    def trigger(patrol_id: str) -> None:
        if patrol_id in triggered_patrol_ids:
            logger.info("patrol_id=%s already triggered; ignoring re-delivery", patrol_id)
            return
        triggered_patrol_ids.add(patrol_id)
        task = asyncio.create_task(run_patrol_pipeline(patrol_id, store, settings))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    return trigger, background_tasks


async def _serve(host: str) -> None:
    """Run the UDP telemetry/event listener and the HTTP event API together.

    Both share one `Store` (see `ingest/store.py`) and both run as
    callbacks on this same asyncio event loop — the UDP transport via
    `create_udp_listener`, the HTTP API via `uvicorn.Server.serve()` awaited
    directly rather than spawned as a separate process. Both are also given
    the same `on_patrol_end` trigger from `_make_patrol_end_trigger`, so a
    `PATROL_END` event newly stored via either channel schedules
    `orchestration.py::run_patrol_pipeline` in the background — see that
    module for what runs once triggered. `await server.serve()` blocks here
    until the process receives a shutdown signal; the `finally` block then
    closes the UDP transport and the `Store`'s SQLite connection. In-flight
    patrol pipeline tasks are not explicitly awaited or cancelled on
    shutdown — `store.close()` running out from under a task already
    mid-`generate_report` call is an accepted risk of process shutdown, no
    different from any other in-flight request being interrupted.

    Called by `main` when the `serve` subcommand is used.
    """
    settings = get_settings()
    store = Store(settings.sqlite_path)
    on_patrol_end, _background_tasks = _make_patrol_end_trigger(store, settings)

    transport, _ = await create_udp_listener(store, host=host, port=settings.UDP_PORT, on_patrol_end=on_patrol_end)
    print(f"UDP telemetry/event listener on {host}:{settings.UDP_PORT}")

    app = create_app(store, on_patrol_end=on_patrol_end)
    config = uvicorn.Config(app, host=host, port=settings.EVENT_PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"HTTP event API on {host}:{settings.EVENT_PORT}")

    try:
        await server.serve()
    finally:
        transport.close()
        store.close()


def _write_images(images: dict[str, bytes], tmp_dir: Path) -> None:
    """Write already-resized image bytes into `tmp_dir/images/{image_id}.jpg`.

    Passed to `storage/layout.py::write_report` via `extra_writers` — same
    reasoning as `pipeline/select_images.py::copy_and_resize_images`
    (`[!FLAG]` in `storage/layout.py`): writing into the *old* report's
    `images/` directory and calling `write_report` afterward does nothing
    for the *new* one, since the atomic swap builds a fresh temp directory
    that only contains what was explicitly written into it.
    `_load_report_images` reads the bytes this writes; the two are always
    called as a pair from `_regenerate`, never independently.
    """
    images_dir = tmp_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for image_id, data in images.items():
        (images_dir / f"{image_id}.jpg").write_bytes(data)


def _load_report_images(report_dir: Path, payload: Payload) -> dict[str, bytes]:
    """Read already-resized images out of a report's own `images/` directory.

    Regeneration has no rover or database access (spec §11) — these
    images were already resized once, when the report was first built
    (`pipeline/select_images.py::copy_and_resize_images`), so there is no
    need to reach the raw source files again even if they were still
    reachable. A missing image file is logged and skipped, same as the
    original build's handling of a missing source (`[!FLAG]`-adjacent
    "never fabricate data on missing input").

    Called only by `_regenerate`.
    """
    images_dir = report_dir / "images"
    images: dict[str, bytes] = {}
    for zone in payload.zones:
        for image_id in zone.image_ids:
            path = images_dir / f"{image_id}.jpg"
            if path.is_file():
                images[image_id] = path.read_bytes()
            else:
                logger.warning("regenerate: image_id=%s missing at %s; continuing without it", image_id, path)
    return images


async def _regenerate(patrol_id: str, report_root: Path, settings: Settings) -> Path:
    """Rebuild `{report_root}/{patrol_id}/` from its own stored `payload.json`.

    Spec §11: "`cli.py regenerate {patrol_id}` re-runs ⑤⑥⑦ from the stored
    payload with no rover involvement." This is the one command in the
    codebase that reconstructs pipeline state from disk instead of running
    ①–④ forward — everything it needs (zone stats, obstruction counts,
    already-selected and already-resized image bytes) was captured in the
    original run's `payload.json` and `images/` directory, so no `Store`,
    no `Settings.DATA_ROOT`, no segmentation, no rover traffic is touched
    at all. `pipeline/payload.py::payload_to_aggregate` reconstructs the
    `PatrolAggregate` `render_report` needs; `_load_report_images` supplies
    the LLM call's vision content.

    Re-runs the real LLM call (⑤) — this is the whole point (spec §11:
    "Essential for prompt tuning — expect dozens of iterations"), not a
    replay of the previous response. `write_report`'s regeneration path
    (A3) already handles overwriting the existing report directory
    atomically, so a failed regenerate leaves the previous report intact.

    `settings` is threaded through by the caller rather than read here via
    `get_settings()`, so a caller holding a non-default `Settings` (tests,
    or a web app constructed with injected settings) doesn't have that
    silently overridden by a fresh env/.env read.

    Returns the final report directory. Called by `main` when the
    `regenerate` subcommand is used.
    """
    report_dir = report_root / patrol_id

    payload = load_payload(report_dir / "payload.json")
    agg = payload_to_aggregate(payload)
    images = _load_report_images(report_dir, payload)
    valid_zone_ids = {z.zone_id for z in agg.zones}

    llm_output, llm_metadata = await generate_report(payload, images, valid_zone_ids, settings)
    agg = agg.model_copy(update={"llm": llm_metadata})

    md = render_report(agg, payload.obstructions, llm=llm_output, coverage_warn_threshold=settings.COVERAGE_WARN_THRESHOLD)

    return write_report(
        patrol_id, md, agg, report_root,
        extra_writers=[
            lambda tmp: write_payload(payload, tmp),
            lambda tmp: _write_images(images, tmp),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses arguments and dispatches to the matching subcommand.

    `serve` runs `_serve(...)` to completion via `asyncio.run`. `regenerate
    {patrol_id}` runs `_regenerate(...)` the same way. Structured as a
    subcommand parser so each stays independent — `regenerate` shares no
    state with `serve` beyond `config.get_settings()`.

    Called by the `ai-report` console script and by `if __name__ == "__main__"` below.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="ai-report")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="run the UDP listener and event API")
    serve_p.add_argument("--host", default="0.0.0.0")

    regen_p = sub.add_parser("regenerate", help="rebuild a report from its stored payload.json")
    regen_p.add_argument("patrol_id")
    regen_p.add_argument("--report-root", default=None, help="defaults to config.REPORT_ROOT")

    args = parser.parse_args(argv)

    if args.command == "serve":
        asyncio.run(_serve(args.host))
    elif args.command == "regenerate":
        settings = get_settings()
        report_root = Path(args.report_root) if args.report_root else settings.REPORT_ROOT
        final_dir = asyncio.run(_regenerate(args.patrol_id, report_root, settings))
        print(f"regenerated report for patrol_id={args.patrol_id} at {final_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
