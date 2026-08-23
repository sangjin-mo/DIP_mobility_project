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
- VIS image management through the existing vision-team server: list received
  images, pull pending Raspberry Pi captures, delete selected PC copies, and
  clean upload-confirmed Pi copies. Enable it with `DASHBOARD_VISION_SERVER_URL`.
- latest zone metadata and LLM Markdown report on the crop report screen
- drive status plus start and immediate-stop commands forwarded to a
  separately configured Raspberry Pi control agent
- KMA ultra-short observation/forecast weather over `/api/weather`, cached so
  dashboard clients do not call the public API independently

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

### Team / multi-laptop access

`web_dashboard` is the single control tower: every laptop's browser talks
only to it, and it forwards drive control, vision capture, and reports to
the other teams' backends. That only works if everyone points at **one
running instance**, not their own local copy.

- **Only one designated Mac/PC runs the backend processes** — `web_dashboard`
  itself, plus `ai_report`'s ingest listener (which is what actually writes
  `sessions.db`) and the vision team's `pc_server`. `scripts/start_central_server.sh`
  starts all three together, opens the macOS firewall for the duration, and
  closes it again on exit.
- **Everyone else only opens a browser.** Do not run `python -m web_dashboard`
  on your own laptop "just to look" — your local `sessions.db`/`reports/`
  will be empty, so the dashboard will look reachable but show none of the
  real data. The header badge next to the logo (대시보드 인스턴스) shows the
  serving machine's hostname and whether it found the shared database; a
  red "로컬 전용" warning there means you're on a stray local instance.
- **Use a stable address, not a DHCP IP.** macOS already advertises this
  machine at `http://<Bonjour hostname>.local:8080` (check yours with
  `scutil --get LocalHostName`) — that name doesn't change between
  sessions the way a DHCP-assigned IP does. This mirrors the
  `raspberry-pi.local` pattern already used below for the Pis.
- `DASHBOARD_HOST` must stay `0.0.0.0` (the default) for other laptops to
  reach it at all — `127.0.0.1` is loopback-only.

Optional `.env` values:

```dotenv
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
DASHBOARD_CAMERA_URL=http://raspberry-pi.local:8889/cam
DASHBOARD_VISION_SERVER_URL=http://127.0.0.1:8000
DASHBOARD_VISION_TIMEOUT_S=35
# Separate webcam Raspberry Pi (not the rover-control Pi).
DASHBOARD_VISION_PI_STATE_URL=http://webcam-pi.local:8002/api/drive-state
DASHBOARD_VISION_PI_STATE_TOKEN=replace-with-a-shared-secret
DASHBOARD_LIVE_POLL_INTERVAL_S=1.0
DASHBOARD_TELEMETRY_STALE_AFTER_S=3.0
DASHBOARD_ROVER_CONTROL_URL=http://raspberry-pi.local:9200/api/control
# Optional; defaults to the /api/status sibling of ROVER_CONTROL_URL.
DASHBOARD_ROVER_STATUS_URL=http://raspberry-pi.local:9200/api/status
DASHBOARD_ROVER_CONTROL_TOKEN=replace-with-a-shared-secret
DASHBOARD_CONTROL_TIMEOUT_S=2.0
DASHBOARD_DEFAULT_TARGET_SPEED_MPS=0.25
DASHBOARD_MAX_TARGET_SPEED_MPS=0.50
DASHBOARD_KMA_SERVICE_KEY=service-key-from-data.go.kr
DASHBOARD_KMA_NX=89
DASHBOARD_KMA_NY=90
DASHBOARD_WEATHER_LOCATION_LABEL=대구광역시 수성구
DASHBOARD_WEATHER_REFRESH_INTERVAL_MINUTES=30
```

The current vision-team server does not publish an HTTP endpoint for sending
images to the LLM team, so `분석팀 전송` remains disabled until its URL,
authentication, request, and response schema are provided. The WEB-owned Pi
receiver supplies a separate capture-mode API without editing vision-team
code. While enabled it saves one JPEG per configured interval into the same
day-based local image directory consumed by the existing upload process.

`VISION_PI_STATE_URL` is a separate event channel for the webcam Pi. Run
`python -m web_dashboard.vision_pi_state_receiver` on that Pi. The dashboard
uses the first observed state only as its local baseline and then sends only
`RUNNING`, `STOPPED`, or `EMERGENCY` transitions. Repeated status polls and
500 ms heartbeats are not forwarded. The receiver stores the latest event in
`vision_drive_state.json`.

Capture-mode Pi options are `VISION_CAPTURE_DIR` (defaults to the existing
`vision/image_transfer/system/pi_agent/images` directory),
`VISION_CAMERA_INDEX` (default `0`), and `VISION_CAPTURE_INTERVAL_S` (default
`1.0`). Do not run the original always-on `capture.py` at the same time as
dashboard capture mode because two processes cannot safely own one webcam.

## KMA weather setup

The weather panel combines two endpoints from the KMA Village Forecast API:

- `getUltraSrtNcst`: outside temperature, humidity, one-hour precipitation,
  precipitation type, and wind speed
- `getUltraSrtFcst`: the nearest sky condition (`맑음`, `구름 많음`, `흐림`)

Apply for `기상청_단기예보 조회서비스` on data.go.kr and put either the
encoded or decoded general service key in the local `.env`. The service
normalises the key before sending it. Convert the fixed farm location to KMA
grid coordinates once and set `DASHBOARD_KMA_NX` and `DASHBOARD_KMA_NY`.
Never commit the real `.env` or service key.

The dashboard fetches `/api/weather` immediately and then at the configured
interval. The server applies the same TTL cache, handles KST base dates/times,
and returns the last successful observation with `is_stale=true` if KMA is
temporarily unavailable. These values describe the outside representative AWS
observation for the forecast grid, not conditions inside a greenhouse.

The weather card maps KMA precipitation and sky states to a matching icon. Its
`새로고침` button calls `POST /api/weather/refresh`, which deliberately bypasses
the TTL cache and requests a fresh KMA response immediately.

The dashboard also requests the KMA ultra-short observations for the current
hour and the preceding 23 hours. The weather screen renders those actual
hourly `T1H` temperature and `RN1` precipitation observations as a line chart
and bar chart without a browser CDN dependency.

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

The vehicle screen sends its selected target speed with `START`. The slider
defaults to `0.25 m/s` and is capped at `DASHBOARD_MAX_TARGET_SPEED_MPS`
(`0.50 m/s` by default, matching the rover's current configured maximum).
Changing the slider while the rover is running sends another validated START
command so the rover updates its speed target without bypassing its own cap.

The dashboard also polls the rover-owned `GET /api/status` endpoint every two
seconds. Start/stop buttons are enabled only while that endpoint is reachable,
and the status pill displays the returned `RUNNING` or `STOPPED` state instead
of treating a configured URL as proof that the rover is online.

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
For the two-Raspberry-Pi deployment order and required addresses, see
[INTEGRATION_RUNBOOK.md](INTEGRATION_RUNBOOK.md).
