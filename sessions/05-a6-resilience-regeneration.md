# Phase A6 — Resilience and regeneration

**Commit:** `c264be1` — "A6: resilience and regeneration"
**Date:** 2026-08-17 01:27

**Goal:** `ai-report regenerate {patrol_id}` must rebuild a report from
**only its own stored `payload.json` plus that report's own `images/`
directory** — no rover, no database, no segmentation access at all.

## What was built

- **A design fix was required first.** `render_report` used to demand a
  full `PatrolSegmentation` object just to call one method on it,
  `.obstruction_counts()` — but regeneration only ever has a stored
  `Payload`, not a segmentation. After checking the actual call sites, the
  parameter was narrowed to just the dict `_build_zone_views` actually
  uses — `Payload.obstructions` is already that exact dict. This also
  simplified the normal (non-regenerate) pipeline path, and ~27 call sites
  in `tests/test_markdown.py` were updated mechanically to match.
- `payload_to_aggregate` — round-trip conversion from a stored `Payload`
  back into the aggregate shape `render_report` needs.
- `cli.py regenerate` command wired up as real, fully production-integrated
  code (unlike the rest of the A2–A5 pipeline, which still has no automatic
  trigger — see `ai_report/CALL_MAP.md`).
- **The same bug class A4 found, hit independently:** `_load_report_images`
  reads the old report's `images/` into memory, but nothing wrote them into
  the new tmp directory before the atomic swap, so they vanished on the
  first attempt. Fixed the same way as A4 — `_write_images` joins
  `write_report(..., extra_writers=[...])`. Same pattern, same root cause,
  caught the same way: an end-to-end smoke test, not a unit test, twice now.
- **Prompt-version-bump enforcement.** A pinned SHA256 hash of
  `SYSTEM_PROMPT` lives in `llm/prompts.py` (alongside
  `PROMPT_VERSION = "v1.0"`), with a test that fails if the prompt text
  changes without the hash being deliberately updated. The mechanism itself
  was verified in both directions, not just written and trusted: the pinned
  hash was deliberately broken on disk and the test confirmed to fail, then
  restored and confirmed to pass again. Hit a real flake while doing this —
  a stale `__pycache__` entry made a `cp`+`sed` revert look like it hadn't
  taken effect (a bytecode-cache/mtime timing issue); diagnosed correctly,
  cache cleared, both directions re-verified clean.
- **Real subprocess proof of the acceptance criterion.** The entire `data/`
  directory was deleted (removing all DB and rover-path access), and
  `ai-report regenerate` was run as an actual subprocess against nothing but
  a report's `payload.json` + `images/` — it produced a complete, valid
  report. The LLM call itself was disabled via env var for this specific
  smoke test rather than making a real network call; the mocked LLM path is
  covered separately by unit tests.

## Verification

139 tests total, ruff clean, `contracts/validate.py` passes.

## Closing assessment for the core build

A1 through A6 — the entire core build plan — was complete at this point. A7
(cross-patrol trend commentary) is explicitly deferred, gated behind
`TREND_MIN_PATROLS = 10` in `config.py`, since a single patrol's worth of
data can't support a trend claim regardless of whether the code exists.

The one substantive thing still missing for real deployment: **no
production orchestration**. Nothing automatically calls the full
`segment → aggregate → select_images → build_payload → generate_report →
render_report → write_report` chain when a patrol actually finishes
(`PATROL_END` + VIS `_COMPLETE`) — documented throughout
`ai_report/CALL_MAP.md`'s "not wired up yet" section. Building this was
offered as the next step; the user didn't take it up in this session — the
conversation pivoted to pushing the finished work to GitHub instead (see
[06-push-and-attribution-scrub.md](06-push-and-attribution-scrub.md)).
