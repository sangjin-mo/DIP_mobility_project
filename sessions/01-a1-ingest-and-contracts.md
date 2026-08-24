# Phase A1 — Ingest and finalized contracts

**Commit:** `9553b47` — "Initial commit: AI subsystem A1 (ingest) plus finalized contracts"
**Date:** built 2026-08-13, committed 2026-08-16 23:34 (git history starts here — no earlier commits existed)

## What was asked

The user's opening prompt was long and structured, in two explicit steps:

**Step 1 — orientation, no code.** Report back: the ownership boundary in one
paragraph; the hard rules from `CLAUDE.md` (now `GUIDELINES.md`) restated in
Claude's own words; any contradiction or gap found across the four spec docs
("I would rather find doc bugs now than in week three"); a concrete
file-by-file plan for A1. Then stop and wait for approval.

**Step 2 — implement A1 only**, after approval:
- Package scaffold: `config.py`, `models.py`
- `ingest/udp_listener.py`, `ingest/event_api.py`, `ingest/vis_watcher.py`,
  `ingest/store.py`
- `devtools/fake_rover.py` and `devtools/fake_vis.py`
- Tests for every A1 acceptance criterion

Explicit constraints:
- Build the fake emitters **alongside** the listeners, not after — every
  later phase develops against synthetic data, since real rover hardware
  wouldn't be available for weeks.
- No network calls in any test.
- Thresholds live in `config.py`, never inline.
- Stop hard at the A1 boundary — no segmentation or aggregation, even if it
  seems like a natural continuation.
- Done-criterion: `fake_rover.py` replays a 20-minute synthetic patrol over
  real UDP to localhost with deliberate packet loss, and the computed loss
  rate must match the configured drop rate within 1%.
- Archive all the `.md` docs plus the original prompt into a `Source Docs/`
  folder once done.

## Gaps flagged before writing any code (Step 1c)

1. **Documented layout didn't match what was on disk.** `GUIDELINES.md`
   pointed at `docs/00`–`04-*.md`, `docs/adr/`, `contracts/schemas/`,
   `contracts/fixtures/`, `contracts/validate.py` — none of it existed yet;
   the docs sat flat at the repo root, the root `README.md` was actually an
   ADR index pointing at eight ADR files that didn't exist, and only one
   schema file (`c1-telemetry`) existed, at the wrong path. Resolved by
   hand-writing `EventMessage`/`AnalysisResult` in `models.py` behind a
   `[!FLAG]` docstring marking them provisional, rather than blocking A1 on
   schema files that didn't exist yet.
2. **"No network calls in any test" vs. A1's own literal UDP-loopback
   acceptance criterion.** Read as "no calls to *external* services," not a
   ban on loopback sockets between the subsystem's own listener and its own
   fake emitter — otherwise A1 would be unsatisfiable by its own rules.
3. **UDP fallback for events was underspecified** — no port named. Decision:
   `udp_listener.py` dispatches on the packet's `type` field over the same
   port (`UDP_PORT = 9100`); event types go through the same idempotent
   `(patrol_id, event_seq)` dedup as the HTTP path.
4. **Self-contradictory golden fixture.** The `patrol_20260813_1430` fixture
   README claimed zone 1's status was `이상`, but its own numbers
   (병충해_의심 ratio 2/14 ≈ 0.143) compute to `주의` under the documented
   status rule. Flagged for A2 (the phase that actually consumes it), not
   fixed yet in A1.
5. **`config.py` scope.** Decided to scaffold the entire documented settings
   surface from spec §3 immediately, rather than growing it phase by phase —
   re-touching the settings boundary repeatedly across six phases seemed
   worse than a few unused fields early.

## What was built

- `ai_report/config.py` — `Settings(BaseSettings)`, `pydantic-settings`,
  `.env`-driven.
- `ai_report/models.py` — boundary models, including the provisional
  hand-written `EventMessage`/`AnalysisResult`.
- `ai_report/cli.py` — `serve` entrypoint.
- `ingest/store.py` — SQLite, three tables, composite-PK dedup, a
  `loss_rate(patrol_id)` query computed as `max(seq)+1` vs. distinct received
  rows.
- `ingest/udp_listener.py` — asyncio datagram endpoint.
- `ingest/event_api.py` — FastAPI `POST /api/events`, idempotent on
  `(patrol_id, event_seq)`.
- `ingest/vis_watcher.py` — polls `data/analysis/{patrol_id}/` for JSON plus
  a `_COMPLETE` marker; raises on an unknown enum `state`.
- `devtools/fake_rover.py` — 20-minute synthetic patrol generator, 1 Hz
  telemetry, configurable `--drop-rate`, six `ZONE_ENTER` + two
  `EMERGENCY_STOP` events over HTTP (with a `--udp-fallback` mode), a
  `--speed` multiplier.
- `devtools/fake_vis.py` — matching analysis JSON output plus `_COMPLETE`.

28 tests, all green; ruff clean. Verified two ways: pytest (all
loopback/in-process, no real sockets across the network) and a real
end-to-end smoke test running `ai-report serve` as an actual background
subprocess with real UDP/HTTP traffic.

### The loss-rate result

Measured loss rate came out to `0.09999999999999998` against a configured
10% drop rate — exact, not just "within 1%" — because
`choose_drop_indices()` in `fake_rover.py` picks the drop set
**deterministically** (seeded, and never drops the last packet, so
`max(seq)+1` always equals the true expected packet count) rather than
relying on statistical convergence over a short run.

## Follow-up work the same day, still pre-commit

- Added docstrings to every function/class/method across the package, and
  created `ai_report/CALL_MAP.md` — a Mermaid diagram of the runtime
  processes plus a per-module call/caller table — after the user asked for
  documentation of "how each function works and an overall map of where and
  which function is called where." It explicitly flags what wasn't wired up
  yet at that point (`VisWatcher.watch`, `Store.loss_rate`,
  `Store.events_for_patrol` — built for A1's own acceptance criteria and
  future A2 callers, with no production caller yet).
- Fixed a Pylance warning (`_protocol` → `_` in `cli.py::_serve`).
- User asked "Where is the AI call? Or the API call?" — clarified there
  wasn't one yet: A1 is ingest-only, and the actual LLM call is stage 5 of a
  7-stage pipeline (`segment → aggregate → select images → build payload →
  LLM call → render → store`), planned for A5, three phases away. Deliberate,
  per hard rule 2 (a full report must be producible with the LLM disabled)
  and the build plan's sequencing — render before LLM, so an API outage
  never blocks the rest of the system.

## The pivotal moment: contracts finalized

The user reported that VIS, DR, and WEB had all given "the go ahead... build
everything, they're just going to match what I build." This meant the
provisional, hand-written `EventMessage`/`AnalysisResult` models from gap #1
could become the real, authoritative contract, since the other teams would
build against whatever this subsystem specified. As a result:

- Wrote `contracts/schemas/c1-event.schema.json` and
  `c2-analysis.schema.json` for real, matching the existing telemetry
  schema's style.
- Wrote `contracts/validate.py`; round-trip validated the schemas against
  actual `fake_rover`/`fake_vis` output.
- Published `contracts/schemas/c3-metadata.schema.json` ahead of A2, so WEB
  could start building against it early.
- Tightened `models.py` so previously-optional fields that the schemas
  actually require (`zone_id`, `env.temp_c`/`humid_pct`, `drive.ultra_cm`,
  `Detection.confidence`) became required keys, matching the schemas
  field-for-field.
- Updated `01-interface-contracts.md`'s status table (C1/C2/C3 → `AGREED`)
  and `04-traceability-matrix.md`.
- Cleaned up stray sandbox debris (`mnt/user-data/outputs/...`).

The session then hit a usage limit mid-A2 ("You've hit your session limit ·
resets 8pm (Asia/Seoul)") and resumed roughly three days later, on
2026-08-16.
