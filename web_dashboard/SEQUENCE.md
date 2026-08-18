# Dashboard sequences and ownership boundaries

## 1. Live telemetry

```text
DR rover
  -> existing ai_report.ingest.udp_listener.TelemetryUDPProtocol
  -> existing ai_report.ingest.store.Store
  -> data/sessions.db
  -> web_dashboard.services.LiveStateService (read only)
  -> FastAPI /ws/live
  -> dashboard.js
```

The dashboard does not bind another UDP port and does not parse incoming rover
datagrams itself.

## 2. Patrol reports

```text
existing AI report pipeline
  -> ai_report.storage.layout.write_report
  -> reports/{patrol_id}/metadata.json + report.md + images/
  -> web_dashboard.services.ReportService
  -> /api/patrols/*
  -> dashboard.js
```

`ReportService` validates metadata through the existing `PatrolAggregate`
model. It does not recalculate status, counts, or environmental statistics.

## 3. Camera

```text
Pi camera -> MediaMTX/WebRTC -> configured camera URL -> dashboard iframe
```

Video does not pass through SQLite or the AI report pipeline.

## 4. Control (not connected)

```text
dashboard -> WEB command API -> agreed DR command adapter -> rover
```

There is no agreed WEB-to-DR command contract in the repository. The draft
therefore renders disabled controls and sends no fabricated command. Once the
DR implementation is available, its existing start/stop functions should be
wrapped rather than reimplemented.

## 5. Missing upstream orchestration

The AI repository still does not automatically run the fresh-report chain on
`PATROL_END` plus VIS `_COMPLETE`. The dashboard can display reports only after
the existing pipeline has produced them. That orchestration should compose the
existing pipeline functions; it does not belong in this WEB package.
