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

> [!FLAG] **A0 status as of 2026-08-13:** DR, VIS, and WEB agreed to implement
> against the AI team's schemas as written (`contracts/schemas/*.json`),
> rather than a separate negotiated round — see `01-interface-contracts.md`'s
> Contract status section. `seq` and `ZONE_ENTER` (DR_104) are both present
> in the agreed schemas. The one open item against this acceptance
> criterion: sign-off is recorded team-level, not with a named person per
> contract — get that if a dispute ever needs a specific owner to resolve it.

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

> [!FLAG] **Done 2026-08-13.** All acceptance criteria met and tested
> (`tests/test_a1_acceptance.py` plus the `ingest`/`devtools` unit tests);
> verified again against a real `ai-report serve` process, not just
> pytest. See `ai_report/CALL_MAP.md` for the module wiring.

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

> [!FLAG] **Done 2026-08-13.** All acceptance criteria met and tested
> (`tests/test_segment.py`, `tests/test_aggregate.py`). `metadata.json` is
> not written to disk by A2 itself — `PatrolAggregate` (the data structure)
> is produced here; `storage/layout.py` (A3) does the actual write, since
> `zones[].image_ids` can't be populated until A4's image selection exists.
> Also surfaced two doc bugs in `contracts/fixtures/patrol_20260813_1430/README.md`
> while building this (self-contradictory expected status, and a
> "drives the flag" claim that contradicted the fixture's own numbers) —
> both fixed. The fallback segmentation path needed config
> (`ROUTE_ZONE_COUNT`, `ROUTE_TOTAL_DISTANCE_M`) the spec never defined —
> see the `[!FLAG]` in `02-ai-subsystem-spec.md` §5.

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

> [!FLAG] **Done 2026-08-13.** All acceptance criteria met and tested
> (`tests/test_markdown.py`, `tests/test_layout.py` — the latter includes
> a monkeypatched mid-write failure to actually prove no partial directory
> is ever observable, not just assert it). A real whitespace bug was found
> and fixed during development: Jinja's `trim_blocks` silently ate the
> newline after any content line ending in `{% endif %}`, concatenating
> zone lines together — fixed by pre-formatting all per-zone strings in
> Python before the template ever runs (see `render/markdown.py`'s module
> docstring). `render_report` takes both `PatrolAggregate` and
> `PatrolSegmentation` as input, not `PatrolAggregate` alone — the 통로 장애
> 요인 section needs per-zone event detail that `metadata.json`'s schema
> deliberately doesn't carry. WEB can now be handed a real `report.md` +
> `metadata.json` pair, produced end to end from a live server in a manual
> smoke test — see `ai_report/CALL_MAP.md`.

---

## A4 — Image selection and payload

**Deliverable:** `pipeline/select_images.py`, `pipeline/payload.py`, writing `payload.json`.

**Acceptance:**
- At most 3 images per zone, selected by the documented priority order
- Images below `image_quality` 0.40 are never selected
- Images resized to 768px long edge and copied into the report directory
- Token estimate computed before any call; over-budget triggers documented degradation
- `payload.json` is complete enough to regenerate a report with no database access

> [!FLAG] **Done 2026-08-17.** All acceptance criteria met and tested
> (`tests/test_select_images.py`, `tests/test_payload.py`). Two real
> issues found and fixed during development, both worth knowing about:
> 1. The "normal representative" step's median must be computed over the
>    *remaining* candidate pool, not every eligible image in the zone —
>    including the already-claimed anomaly exemplar (whose 정상 count is
>    usually 0) skews "typical" toward zero. Spec §7 doesn't specify which
>    population the median is over; see `select_images_for_zone`'s inline comment.
> 2. A genuine data-loss bug, caught by the end-to-end smoke test rather
>    than a unit test: writing selected images to the final report path
>    and *then* calling `storage/layout.py::write_report` silently
>    discards them, because `write_report`'s atomic swap builds a fresh
>    temp directory containing only `report.md`/`metadata.json` and
>    renames it over the final path. Fixed by adding `write_report(...,
>    extra_writers=[...])` — see the `[!FLAG]` in `storage/layout.py`.
>    `pipeline/select_images.py::copy_and_resize_images` and
>    `pipeline/payload.py::write_payload` must be passed through this
>    parameter, never called against the final path directly.
>
> Also: `fake_vis.py` now generates real (if content-free) JPEG placeholder
> images instead of empty files, since `copy_and_resize_images` needs
> something Pillow can actually decode.

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
