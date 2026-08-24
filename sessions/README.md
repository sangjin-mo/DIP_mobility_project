# Sessions

Reconstructed record of the Claude Code sessions that built the `ai_report` AI
subsystem end to end, from Phase A1 (ingest) through A6 (resilience and
regeneration), plus the post-build push/attribution work. Written after the
fact from git history, repo docs, and the raw session transcripts — not
captured live — so treat it as a faithful reconstruction rather than a
verbatim log.

**Timeline:** 2026-08-13 (A1) → ~3-day gap (session usage limit) →
2026-08-16 to 2026-08-17 (A2 through A6, then the GitHub push saga and the
`CLAUDE.md` → `GUIDELINES.md` rename).

**Working pattern:** the user opened with one long, structured prompt (an
explicit orientation step before any code, then a scoped A1-only
implementation with hard stop conditions). After A1 was verified end-to-end
and the other three teams (DR, VIS, WEB) signed off on building against
whatever this subsystem specified, the user switched to short continuation
commands ("Continue.", "Commit.", "Go ahead.") and let the build run through
all six phases in one push, with Claude pausing at each phase boundary to
report status and ask whether to continue.

## Phase index

| # | File | Commit | What it covers |
|---|---|---|---|
| 1 | [01-a1-ingest-and-contracts.md](01-a1-ingest-and-contracts.md) | `9553b47` | UDP/HTTP ingest, SQLite store, fake rover/VIS devtools, finalized boundary contracts |
| 2 | [02-a2-a3-segmentation-rendering-storage.md](02-a2-a3-segmentation-rendering-storage.md) | `bfbeb1f` | Event-based zone segmentation, deterministic aggregation, Markdown rendering, atomic storage |
| 3 | [03-a4-image-selection-payload.md](03-a4-image-selection-payload.md) | `13efc87` | Three-tier image selection, payload construction, the first atomic-swap data-loss bug |
| 4 | [04-a5-llm-integration.md](04-a5-llm-integration.md) | `be8967d` | Structured-output schema, prompt, retry/backoff LLM client, reworked rendering |
| 5 | [05-a6-resilience-regeneration.md](05-a6-resilience-regeneration.md) | `c264be1` | `ai-report regenerate`, prompt-version hash pinning, the second atomic-swap bug |
| 6 | [06-push-and-attribution-scrub.md](06-push-and-attribution-scrub.md) | `fd58e09` | GitHub push saga, access issues, git-history rewrite, `CLAUDE.md` → `GUIDELINES.md` rename |
| 7 | [07-drift-reconciliation-and-classification-fixes.md](07-drift-reconciliation-and-classification-fixes.md) | `4fe6611` | Pi network watchdog, ADR-0010, doc-drift reconciliation, concurrent classification, the mtime bug that emptied every report |

## Cross-cutting decisions

These recur across phases or set precedent for later ones — see the phase
files for full context.

| Decision | Rationale |
|---|---|
| Hand-write `EventMessage`/`AnalysisResult` in A1, flagged provisional | No schema files existed yet to generate from; blocking A1 on missing schemas was worse than an isolated, flagged assumption. Later became the real, authoritative contract once other teams agreed to build against it. |
| Loopback UDP / in-process ASGI calls treated as compliant with "no network calls in tests" | Otherwise A1's own literal acceptance criterion (a real UDP loopback test) would be unsatisfiable |
| `config.py` scaffolded with the full eventual settings surface in A1, not grown phase-by-phase | Cheap to do up front; avoids repeatedly re-touching the settings boundary in every later phase |
| Zone status rule checks 이상 (abnormal) before 주의 (caution) | A zone meeting both severity conditions must report the more severe status |
| Jinja template content lines use `{{ }}` interpolation only, never end a line in `{% %}` | `trim_blocks` silently ate the newline after lines ending in `{% endif %}`, running zone sections together; pre-formatting strings in Python sidesteps Jinja's whitespace-control edge cases entirely |
| `write_report(..., extra_writers=[...])` added to `storage/layout.py` | The atomic swap builds a fresh temp directory from scratch — anything written to the final report path *before* the swap runs (images, `payload.json`) is silently destroyed. Found independently in both A4 and A6 via end-to-end smoke tests, never by a unit test. |
| "Normal representative" image selection excludes the already-claimed anomaly exemplar from its candidate pool | Including a forced-zero-count image in the median calculation skews "typical" toward zero |
| OpenAI SDK pinned to `>=1.30,<2.0` explicitly | GUIDELINES.md pins the stack to "v1.x"; the environment defaulted to a newer major version whose API surface couldn't be verified against known behavior |
| LLM retry: 4 attempts total, 2s/4s/8s exponential backoff, HTTP 400 never retried | Matches spec §9 literally; 400 is a malformed-request signal retrying cannot fix |
| Every LLM failure path degrades to `LlmMetadata(enabled=False)` rather than raising | Hard rule 2 requires a full report to be producible with the LLM entirely disabled |
| `render_report`'s segmentation parameter narrowed to just the dict `_build_zone_views` needs | A6's `regenerate` path has no `PatrolSegmentation`, only a stored `Payload`; the narrower signature also simplified the normal pipeline |
| Prompt-version drift caught via a pinned SHA256 hash test in `llm/prompts.py`, verified in both directions | A silent prompt edit without a version bump would break reproducibility; the test itself was deliberately broken and restored once to prove it actually fails on drift, not just exists |
| Git history rewritten with `git filter-repo` to strip `Co-Authored-By: Claude` trailers; `CLAUDE.md` renamed to `GUIDELINES.md` everywhere, including the `Source Docs/` archive | Explicit user instruction: "I don't want you as a contributer" |

## Known open items (as of 2026-08-17)

- **No production orchestration exists.** Nothing currently calls
  `segment_patrol → aggregate → apply_image_selection → build_payload →
  llm.client.generate_report → render_report → write_report` automatically
  when a patrol finishes (`PATROL_END` + VIS `_COMPLETE`). Only
  `ai-report regenerate {patrol_id}` (A6) is fully wired production code, and
  it's a narrower path that only works once a `payload.json` already exists
  from an earlier run. See `ai_report/CALL_MAP.md`'s "What's not wired up
  yet" section.
- **`docs/adr/` does not exist.** The root `README.md` is an ADR index
  listing eight ADRs (0001–0008) — including load-bearing ones like "the LLM
  never computes numbers" and "segment zones by event, not elapsed time" —
  but none of those ADR files exist anywhere in the repo. This gap was
  flagged in the very first A1 orientation report and never resolved.
- **A7 (cross-patrol trend commentary) is deferred**, gated behind
  `TREND_MIN_PATROLS = 10` in `config.py` — there isn't enough patrol history
  yet regardless of code readiness.
- **Documented layout still doesn't match disk.** GUIDELINES.md's document
  map points at `docs/00`–`04-*.md`; those files actually live flat at the
  repo root (and are duplicated again under `Source Docs/`).
- **Final push to `sangjin-mo/DIP_mobility_project` was left for the user to
  run manually** after a non-fast-forward rejection (the remote had diverged,
  presumably from the collaborator's own commits). Whether it succeeded isn't
  captured in the available transcripts.

## Status update (as of 2026-08-25)

Two of the 08-17 open items above have moved:

- **Production orchestration now exists.** `ai_report/orchestration.py::run_patrol_pipeline`
  runs the full chain on `PATROL_END`, wired through `cli.py::_serve`. Zones
  come from `segment_by_crop_type` (ADR-0009), not `segment_patrol` — DR's
  `drive_ver2` never sends `ZONE_ENTER`.
- **The ADR gap is now documented rather than silent.** 0001–0008 still do not
  exist; `04-traceability-matrix.md` and the root `README.md` index both flag
  it. `ADR-0009` and `ADR-0010` are the only records that exist as files.

The rest of the 08-17 list stands. See
[07-drift-reconciliation-and-classification-fixes.md](07-drift-reconciliation-and-classification-fixes.md)
for the current open items.
