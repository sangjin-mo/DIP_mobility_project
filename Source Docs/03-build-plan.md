# 03 — Build Plan

Phases are ordered so that each one is independently demonstrable and nothing is blocked on another team's hardware.

## Two sequencing decisions worth understanding

**A3 (Markdown rendering) comes before A5 (the LLM call).** Building a complete report with no LLM first means: WEB can start integrating at A3 rather than waiting for the whole pipeline, the fallback report required by A6 already exists, and an OpenAI outage or a billing problem never blocks project progress.

**A1 includes the fake rover.** Every later phase is developed and tested against synthetic data. Without it, this subsystem cannot start until DR and VIS both work — which is the schedule risk that sinks four-team projects.

---

## A0 — Contracts

**Deliverable:** `docs/01-interface-contracts.md` reviewed and signed off by DR, VIS, and WEB.

This is not a coding phase and it is the highest-risk item in the plan. Every later phase encodes these schemas.

Specific things to secure:

- **From DR:** the `seq` field, and `ZONE_ENTER` events (requirement DR_104). These two are non-negotiable. Everything else in C1 can flex.
- **From VIS:** the closed four-value state enum, explicit `판단불가` reporting, and `image_quality` on every result.
- **From WEB:** agreement that they read `metadata.json` for zone status rather than parsing numbers out of the Markdown.

**Acceptance:** all three contracts marked `AGREED` in the status table, with a named person per contract.

---

## A1 — Ingest and fake data

**Deliverable:** UDP listener, event API, VIS watcher, SQLite store, and `devtools/fake_rover.py` + `devtools/fake_vis.py`.

**Acceptance:**
- `fake_rover.py` replays a 20-minute synthetic patrol over real UDP to `localhost:9100`, including 6 `ZONE_ENTER` events, 2 `EMERGENCY_STOP`s, and deliberate packet loss
- Duplicate packets are silently ignored (primary-key dedup)
- Out-of-order arrival is handled correctly
- `udp_expected` computed as `max(seq)+1` matches the emitter's count
- Malformed packets are logged and dropped without crashing the listener
- Loss rate is computed and matches the emitter's configured drop rate within 1%

---

## A2 — Segmentation and aggregation

**Deliverable:** `pipeline/segment.py`, `pipeline/aggregate.py`, writing `metadata.json`.

**Acceptance:**
- Zones are segmented from `ZONE_ENTER` events; a mid-zone emergency stop does **not** shift any zone boundary
- Missing `ZONE_ENTER` events trigger fallback segmentation with `boundary_confidence: "low"`
- Running `aggregate()` twice on identical input produces byte-identical output
- An unknown `state` value from VIS raises rather than being coerced
- Zone status follows the deterministic rule in spec §6, verified against hand-computed fixtures
- `재촬영_필요` appears exactly when `undetermined_rate > 0.30`
- A zone with zero observations produces valid output with `undetermined_rate: null`

The emergency-stop test is the important one. Write it first.

---

## A3 — Markdown rendering, no LLM

**Deliverable:** `render/markdown.py`, the Jinja template, `storage/layout.py`, and a working `report.md` produced with `LLM_ENABLED = False`.

**Acceptance:**
- All six H2 sections present in the contracted order
- Every number in the output traces to `aggregate()`
- Atomic write verified: no partial directory is ever observable
- WEB team can parse the output and render it
- Report generates successfully for a patrol with zero images and for one with a single zone

**At the end of A3 the subsystem is already useful.** Hand it to WEB and start their integration.

---

## A4 — Image selection and payload

**Deliverable:** `pipeline/select_images.py`, `pipeline/payload.py`, writing `payload.json`.

**Acceptance:**
- At most 3 images per zone, selected by the documented priority order
- Images below `image_quality` 0.40 are never selected
- Images resized to 768px long edge and copied into the report directory
- Token estimate computed before any call; over-budget triggers documented degradation
- `payload.json` is complete enough to regenerate a report with no database access

---

## A5 — LLM integration

**Deliverable:** `llm/client.py`, `llm/prompts.py`, `llm/schema.py`; full reports with prose.

**Acceptance:**
- Strict structured output; a schema violation is caught, not silently accepted
- Response contains no numeric claims that contradict the aggregate
- Prompt prohibitions verified against adversarial fixtures:
  - a zone with high humidity and high disease count does **not** produce causal language
  - a zone with `undetermined_rate > 0.30` produces a re-capture recommendation, not a diagnosis
  - output uses 관측 수, never 개체 수
- `input_tokens`, `output_tokens`, `cost_usd`, `model`, `prompt_version` recorded in `metadata.json`
- Unknown `zone_id` in the response is dropped and logged
- All tests mock the client; no test hits the network

---

## A6 — Resilience and regeneration

**Deliverable:** retry logic, fallback path, `cli.py regenerate`, prompt versioning.

**Acceptance:**
- Simulated 429 retries three times with exponential backoff, then falls back
- Fallback report has all six sections and `llm.enabled: false`
- `regenerate {patrol_id}` produces a fresh report from stored `payload.json` with no rover or database dependency
- Editing the prompt without bumping `PROMPT_VERSION` fails a test

`regenerate` matters more than its size suggests. Prompt tuning takes dozens of iterations and re-running the rover for each is not viable.

---

## A7 (optional, v2) — Cross-patrol trends

Deferred, but the schema already supports it. Compare zone metrics across the last N patrols and describe change over time.

**Do not enable trend commentary below `TREND_MIN_PATROLS` (10).** Correlation claims from a handful of patrols are the same statistical error the prompt prohibitions exist to prevent — this is where genuine signal finally becomes available, and it should not be spent early.

---

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| DR declines `ZONE_ENTER` events | Reports silently mislabelled | Fallback segmentation exists but degrades loudly; escalate at A0 |
| VIS state enum drifts | Aggregation raises | Validation at the boundary catches it immediately rather than corrupting output |
| Clock skew between rover and PC | Image-telemetry mismatch | NTP; monotonic fallback documented in C1.4 |
| Contracts change after A4 | Rework | Pydantic models isolate every boundary assumption |
| Hardware late | Blocked development | Fake emitters from A1 remove the dependency entirely |
| LLM output quality poor | Weak reports | `regenerate` makes prompt iteration cheap; prohibitions are testable |
