"""HTTP event ingest (C1.2) — the primary, reliable-delivery channel for events.

FastAPI validates the request body against `EventMessage` before this
handler ever runs, so a malformed body is rejected with 422 automatically.
Idempotency (receiving the same event_seq twice is a no-op) comes from the
store's INSERT OR IGNORE on the (patrol_id, event_seq) primary key.

Called by:
- `cli.py::_serve` — mounts the returned app behind uvicorn for the real
  `ai-report serve` process.
- `devtools/fake_rover.py::_post_event_http` — sends the real
  `POST /api/events` requests this module receives, when not using
  `--udp-fallback`.
- `tests/test_event_api.py` — drives the app in-process via FastAPI's
  `TestClient` (no real socket).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from ai_report.ingest.store import Store
from ai_report.models import EventMessage

logger = logging.getLogger(__name__)


def create_app(store: Store) -> FastAPI:
    """Build a FastAPI app exposing `POST /api/events`, bound to `store`.

    A factory rather than a module-level `app = FastAPI()` singleton so
    every caller (the real server, every test) supplies its own `Store` and
    the handler closes over it — this keeps tests fully isolated (a fresh
    SQLite file per test) without any global state.

    Called by `cli.py::_serve` and directly by `tests/test_event_api.py`.
    """
    app = FastAPI(title="AI Report — Event Ingest")

    @app.post("/api/events", status_code=202)
    async def post_event(event: EventMessage) -> dict:
        """Handle one event POST: store it (idempotently) and report duplicate status.

        FastAPI parses and validates the JSON body into `event: EventMessage`
        before this function body runs — an invalid body never reaches this
        line, it short-circuits to a 422 response. Called by FastAPI's
        routing for every request to `POST /api/events`; calls
        `Store.insert_event`, whose return value (False on primary-key
        collision) becomes the `duplicate` field in the response.
        """
        inserted = store.insert_event(event)
        if not inserted:
            logger.info(
                "duplicate event ignored: patrol_id=%s event_seq=%s",
                event.patrol_id,
                event.event_seq,
            )
        return {"status": "accepted", "duplicate": not inserted}

    return app
