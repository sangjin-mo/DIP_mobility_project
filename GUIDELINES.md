# GUIDELINES.md

Read this first. It defines what to build, what **not** to build, and the rules that must hold in every change.

## What this repository is

The **AI Report Subsystem** for a smart agricultural patrol rover project. A Waveshare PiRacer rover (Raspberry Pi 4, DonkeyCar-compatible) drives a fixed line-following route through a greenhouse, capturing images and environmental sensor data. This subsystem runs on the base-station PC. Once per patrol it collects everything the rover produced, aggregates it deterministically, sends a bounded payload to a multimodal LLM, and writes a Korean-language Markdown report plus structured JSON metadata for a web dashboard to display.

This is a **batch pipeline**, not a real-time service. Nothing here sits in a control loop. The rover drives safely with this subsystem completely offline.

## Ownership boundary — read carefully

This is a four-team project. **You implement only the AI subsystem.** The other three exist and are owned by other people.

| Subsystem | Owner | You may |
|---|---|---|
| **AI** — telemetry ingest, aggregation, LLM report | **us** | implement freely |
| **DR** — rover driving, line following, sensors | other team | read the contract, write fake emitters for testing |
| **VIS** — YOLO crop detection and classification | other team | read the contract, write fake emitters for testing |
| **WEB** — dashboard, live stream, scheduling | other team | write files to the agreed output layout |

### Explicit non-goals — do not implement these

- **No YOLO, no object detection, no image classification.** VIS delivers finished analysis JSON. Consuming it is the entire job.
- **No web dashboard, no HTML, no frontend.** WEB reads our output files. We never render UI.
- **No rover control, no motor commands, no GPIO, no serial.** We receive data; we never send driving commands.
- **No live video streaming.** WEB handles that directly with the rover.
- **No scheduling / cron / APScheduler.** WEB triggers patrols. We react to `PATROL_END`.

If a task seems to require one of the above, stop and say so rather than building it. The correct move is almost always a fake emitter in `devtools/` instead.

## Document map

| Document | Read it when |
|---|---|
| `docs/00-system-overview.md` | You need context on the whole system and vocabulary |
| `docs/01-interface-contracts.md` | You touch anything crossing a subsystem boundary — prose commentary on the schemas |
| `docs/02-ai-subsystem-spec.md` | You implement any module — module layout, algorithms, prompt, output schema |
| `docs/03-build-plan.md` | You start a phase — sequencing and acceptance criteria |
| `docs/04-traceability-matrix.md` | You finish a requirement — update its row; check nothing is orphaned |
| `docs/adr/` | You wonder *why* something is built this way, or want to change it |
| `contracts/schemas/` | **Authoritative for every boundary message.** Generate models from these |
| `contracts/fixtures/` | You write a test touching a boundary — use these, not invented data |

**Source-of-truth order:** `contracts/schemas/` beats `docs/01-interface-contracts.md` beats everything else. The prose is commentary; the schema is the contract.

Before changing a decision recorded in `docs/adr/`, read that ADR. If you still want to change it, write a new ADR that supersedes it rather than editing the old one.

## Hard rules

These are invariants. A change that breaks one is wrong even if tests pass.

1. **The LLM never computes numbers.** Every count, average, ratio, and percentage is computed in Python before the API call. The model receives finalized figures and writes interpretation only. Numbers in the rendered Markdown come from the aggregation step, never from model output.
2. **Aggregation is deterministic and independently runnable.** `aggregate()` must produce identical output for identical input, with no network calls. A full report must be producible with the LLM disabled.
3. **Structured output only.** The LLM returns JSON against a strict schema. Markdown is rendered from a Jinja template. Never ask the model for Markdown.
4. **Zone assignment comes from events, never from elapsed time.** See `docs/01-interface-contracts.md` §C1 and `docs/02-ai-subsystem-spec.md` §5. This is the single most important correctness rule in the system.
   > **Amended by ADR-0009** (`ADR-0009-llm-inferred-crop-zones.md`): `drive_ver2` never sends `ZONE_ENTER` or any other event/telemetry, so a third path, `pipeline/segment.py::segment_by_crop_type`, groups zones by classified crop type instead. The rule's intent — never fabricate a zone boundary from elapsed time — still holds; crop-type grouping doesn't use elapsed time at all. Read the ADR before touching `orchestration.py`'s choice of segmentation function.
5. **Never fabricate data on missing input.** Absent, lost, or low-quality data is reported as a limitation in the output. Coverage and packet-loss figures are part of the report, not hidden.
6. **Never write outside the output directory.** Other teams own their paths.
7. **API keys come from the environment.** Never hardcoded, never logged, never committed.
8. **Boundary models are generated from `contracts/schemas/`, not hand-written.** A hand-written model drifts from the schema silently. See ADR-0008.

## Stack and conventions

- Python 3.11+, `uv` or `venv`
- **Pydantic v2** for every schema at a boundary — parse at the edge, use typed objects internally
- **SQLite** (stdlib `sqlite3`) for telemetry rows; plain files for report outputs
- **Jinja2** for Markdown rendering
- **OpenAI Python SDK v1.x**
- `pytest` for tests, `ruff` for lint
- Standard-library `logging`, never `print` outside `cli.py`

**Language:** code, comments, identifiers, and docs in English. Korean only for: report prose, the LLM system prompt, domain enum values (`정상`, `병충해_의심`), and rendered Markdown headings. Never mix languages inside one identifier.

**Config:** `pydantic-settings`, loaded from `.env`. No magic numbers in module bodies — thresholds live in `config.py`.

## Working agreement

- Prefer small, verifiable steps. Each phase in `docs/03-build-plan.md` has acceptance criteria; satisfy them before moving on.
- Every module that crosses a contract boundary gets a test using the golden fixtures in `contracts/fixtures/`, not invented data and not live services.
- `python contracts/validate.py` must pass before any commit touching a schema or fixture.
- If a contract in `docs/01-interface-contracts.md` is marked `PROPOSED`, code against it but isolate the assumption behind a Pydantic model so a change is one edit.
- When you find an ambiguity the docs don't settle, write the assumption into the relevant doc as a `> [!FLAG]` block rather than silently choosing.

## Definition of done

- Types pass, `ruff` clean, tests green
- No network calls in any test
- New thresholds landed in `config.py`, not inline
- If a boundary schema changed: schema, fixtures, and `docs/01-interface-contracts.md` updated in the same change, and `contracts/validate.py` passes
- The relevant row in `docs/04-traceability-matrix.md` updated
- If an architectural decision was made or reversed, an ADR written in `docs/adr/`
