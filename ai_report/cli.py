"""Entry points. This is the only module allowed to use `print`.

Called by: the `ai-report` console script (registered in `pyproject.toml`),
or directly via `python -m ai_report.cli serve`. Calls `get_settings`
(config.py), `Store` (ingest/store.py), `create_udp_listener`
(ingest/udp_listener.py), and `create_app` (ingest/event_api.py) to wire up
the whole A1 ingest path in one process.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn

from ai_report.config import get_settings
from ai_report.ingest.event_api import create_app
from ai_report.ingest.store import Store
from ai_report.ingest.udp_listener import create_udp_listener


async def _serve(host: str) -> None:
    """Run the UDP telemetry/event listener and the HTTP event API together.

    Both share one `Store` (see `ingest/store.py`) and both run as
    callbacks on this same asyncio event loop — the UDP transport via
    `create_udp_listener`, the HTTP API via `uvicorn.Server.serve()` awaited
    directly rather than spawned as a separate process. `await server.serve()`
    blocks here until the process receives a shutdown signal; the `finally`
    block then closes the UDP transport and the `Store`'s SQLite connection.

    Called by `main` when the `serve` subcommand is used.
    """
    settings = get_settings()
    store = Store(settings.sqlite_path)

    transport, _ = await create_udp_listener(store, host=host, port=settings.UDP_PORT)
    print(f"UDP telemetry/event listener on {host}:{settings.UDP_PORT}")

    app = create_app(store)
    config = uvicorn.Config(app, host=host, port=settings.EVENT_PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"HTTP event API on {host}:{settings.EVENT_PORT}")

    try:
        await server.serve()
    finally:
        transport.close()
        store.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses arguments and dispatches to the matching subcommand.

    Currently the only subcommand is `serve`, which runs `_serve(...)` to
    completion via `asyncio.run`. Structured as a subcommand parser (rather
    than a single flat set of flags) so future phases can add
    `regenerate {patrol_id}` (A6) without reshaping this function.

    Called by the `ai-report` console script and by `if __name__ == "__main__"` below.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="ai-report")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="run the UDP listener and event API")
    serve_p.add_argument("--host", default="0.0.0.0")

    args = parser.parse_args(argv)

    if args.command == "serve":
        asyncio.run(_serve(args.host))
    return 0


if __name__ == "__main__":
    sys.exit(main())
