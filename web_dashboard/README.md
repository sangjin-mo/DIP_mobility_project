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
- drive status plus start and immediate-stop commands forwarded to a
  separately configured Raspberry Pi control agent

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
DASHBOARD_ROVER_CONTROL_URL=http://raspberry-pi.local:9200/api/control
DASHBOARD_ROVER_CONTROL_TOKEN=replace-with-a-shared-secret
DASHBOARD_CONTROL_TIMEOUT_S=2.0
DASHBOARD_DEFAULT_TARGET_SPEED_MPS=0.25
```

## Drive control contract

The browser calls this dashboard only. The dashboard then sends one JSON
request to `DASHBOARD_ROVER_CONTROL_URL`:

```json
{
  "command_id": "6fab3f62-7d61-45aa-b0fb-389052a26f11",
  "command": "START",
  "sent_at_ms": 1787200000000,
  "target_speed_mps": 0.25
}
```

`command` is `START`, `STOP`, or `HEARTBEAT`. `STOP` is the single red safety
stop exposed to users and immediately commands zero throttle. While the
vehicle is running, the browser sends a heartbeat every 500 ms. The rover must
drop throttle to zero locally if no heartbeat arrives within its configured
timeout. The rover must return
`{"accepted": true, "state": "RUNNING"}` only after its actuator owner has
accepted the command. A successful HTTP send alone is not treated as a
successful drive command.

The real Raspberry Pi agent must remain the sole owner of PWM/GPIO, enforce a
heartbeat timeout, and stop the motors locally when communication is lost.
This repository does not guess the conversion from m/s to throttle; that value
must be calibrated on the actual PiRacer before motor testing.

For dashboard integration without motors, run the fake agent in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn web_dashboard.devtools.fake_control_agent:app --host 127.0.0.1 --port 9200
```

Then set `DASHBOARD_ROVER_CONTROL_URL=http://127.0.0.1:9200/api/control` and
run the dashboard. The buttons will exercise the complete HTTP command path
without touching hardware.

## Tests

```powershell
python -m pytest -q web_dashboard/tests
```

See [SEQUENCE.md](SEQUENCE.md) for integration boundaries and runtime flows.
