# Lightweight WEB dashboard draft

This directory is intentionally separate from `ai_report/`. It is an initial
WEB-team surface, not a replacement for the existing AI implementation.

## Reused existing code

- `ai_report.config.Settings`: `DATA_ROOT`, `REPORT_ROOT`, and `sqlite_path`
- `ai_report.models.TelemetryPacket` / `EventMessage`: SQLite rows are parsed
  back into the existing boundary models
- `ai_report.models.PatrolAggregate`: every `metadata.json` is validated before
  the dashboard returns it
- `ai_report.ingest.store.Store` schema: the dashboard opens the same
  `sessions.db` read-only; it does not create a competing writer
- `reports/{patrol_id}/`: generated Markdown, metadata, and selected images are
  consumed exactly as written by `ai_report.storage.layout.write_report`

The original UDP listener, aggregation rules, LLM client, renderer, and storage
pipeline are not duplicated here.

## Current draft scope

- one responsive HTML/CSS/JavaScript dashboard
- latest telemetry and event over `/ws/live`
- patrol list, metadata, Markdown, and selected-image APIs
- camera placeholder, enabled by `DASHBOARD_CAMERA_URL`
- visibly disabled control buttons until the DR control contract exists

The WebSocket currently polls the AI-owned SQLite file once per second. This is
deliberate for the first draft: it avoids editing or duplicating
`TelemetryUDPProtocol`. A later integration can add a small publish hook to the
existing listener while preserving this WebSocket response shape.

## Run

From the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m web_dashboard
```

Open `http://127.0.0.1:8080`.

Optional `.env` values:

```dotenv
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
DASHBOARD_CAMERA_URL=http://raspberry-pi.local:8889/cam
DASHBOARD_LIVE_POLL_INTERVAL_S=1.0
DASHBOARD_TELEMETRY_STALE_AFTER_S=3.0
```

## Tests

```powershell
python -m pytest -q web_dashboard/tests
```

See [SEQUENCE.md](SEQUENCE.md) for integration boundaries and runtime flows.
