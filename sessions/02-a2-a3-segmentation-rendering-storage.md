# Phase A2 + A3 — Segmentation/aggregation and Markdown rendering/atomic storage

**Commit:** `bfbeb1f` — "A2 (segmentation + aggregation) and A3 (Markdown rendering + atomic storage)"
**Date:** 2026-08-16 23:52
**Preceded by:** a git-init on session resume (see below), after a ~3-day gap
since A1.

## Session resume and a recurring safety habit

On resuming the session on 2026-08-16, `git status` failed — the repo wasn't
yet a git repository. Claude asked whether to `git init` and make an initial
commit; the user replied "Commit." then "Continue.", and the rest of A2/A3
was built under that short-command pattern.

Worth noting explicitly: throughout the rest of the build, Claude repeatedly
and deliberately reasoned about whether tool output (grep results, pytest
output, file contents) could be disguised prompt injection, re-confirming
each time that the only real new user turns were short continuation commands
and that observed tool output was consistent with continuing that
instruction — never treating tool output itself as a new instruction. This
pattern recurs at nearly every phase transition in the session.

Housekeeping: found and removed a stray `.DS_Store` that had gotten staged
before the first commit; `.gitignore` already correctly excluded `.venv/`,
`__pycache__/`, `data/`, `reports/`, `.env`.

## A2 — Segmentation and aggregation

- `pipeline/segment.py` — primary path assigns zones from `ZONE_ENTER` /
  `PATROL_END` **events**, never elapsed time (hard rule 4, called out in
  GUIDELINES.md as the single most important correctness rule in the
  system). A distance-based fallback path exists for when no
  `ZONE_ENTER` events are present at all — this fallback wasn't specified
  anywhere in the docs, so new config was added and the gap flagged:
  `ROUTE_ZONE_COUNT = 6`, `ROUTE_TOTAL_DISTANCE_M = 120.0` in `config.py`.
  Verified against 1200 test packets, correctly and evenly zone-assigned;
  the fallback-exclusion logic is separately tested.
- `pipeline/aggregate.py` — deterministic per-zone stats and the status
  rule. Two literal thresholds from spec §6 became named constants:
  `_ABNORMAL_DISEASE_RATIO = 0.15` (이상), `_CAUTION_DISEASE_RATIO = 0.05`
  (주의). **Order matters**: 이상 is checked before 주의, so a zone meeting
  both conditions reports the more severe status. `disease_ratio` divides by
  정상 + 미성숙 + 병충해_의심 counts, excluding 판단불가 (undetermined) from
  the denominator.
- Fixed the golden-fixture bug flagged back in A1's orientation report
  (zone 1's claimed `이상` status didn't match its own numbers), and while
  fixing it, found a **second** contradiction in the same fixture: the
  README claimed `z1_003` "drives the `재촬영_필요` flag," but its own
  numbers sit exactly on the 0.30 boundary where the flag does *not* fire.
  Both fixed before writing tests that assert the correct values.
- A real Python-semantics bug caught before it ever shipped: verified how
  `str, Enum` members render under Python 3.13 before finalizing the Jinja
  template — confirmed `str(Enum)` gives `"ReportStatus.CAUTION"`, not
  `"주의"`. Every enum reference in the template had to use `.value`
  explicitly.
- 56 tests total after A2, all green, ruff clean.

## A3 — Rendering and atomic storage

- `render/markdown.py` — six fixed sections rendered via Jinja directly from
  the aggregate. No LLM involved at all in A3, deliberately, per hard rules
  2 and 3 (a full report must be producible with the LLM fully disabled;
  `LlmMetadata(enabled=False)` is the placeholder shape).
- **A real Jinja whitespace bug found and fixed during smoke-testing:**
  `trim_blocks` was eating the newline after *any* line ending in
  `{% endif %}`, not just pure block-tag lines, silently running several
  zone sections together in the output. Rather than fight Jinja's
  whitespace-control rules line by line, the fix changed strategy entirely:
  pre-format each zone's content as plain Python strings, and let the
  template do simple `{{ }}` interpolation only — never end a content line
  in `{% %}`.
- `storage/layout.py` — the **atomic write** pattern this whole system
  depends on: build a fresh temp directory, then rename/swap it into place.
  Proven with actual mid-write-failure tests, not just asserted.
- A test-authoring bug (not a code bug) surfaced: a test set boundary
  confidence only on a separate `segmentation` object, while `render_report`
  correctly reads it from `agg.data_completeness` — the real source of
  truth, since `aggregate()` always derives that field from
  `segmentation.boundary_confidence`. Fixed the test, not the code.
- A real end-to-end smoke test at A1/A2/A3 rigor: an actual `ai-report
  serve` subprocess, real UDP/HTTP traffic, the full pipeline producing a
  real report on disk. While debugging this, an earlier smoke-test process
  (from the pre-gap A1 run) was found still alive in the background and had
  to be killed — explicitly reasoned through as mundane process leakage, not
  a security concern, before proceeding.
- 78 tests total after A2+A3, ruff clean, `contracts/validate.py` passes.
  `03-build-plan.md` and `04-traceability-matrix.md` updated;
  `ai_report/CALL_MAP.md` updated with the new sections.

## Checkpoint

Claude's own framing at this point: "A3 is a legitimate stopping point,
since the subsystem now produces a complete, useful report with no OpenAI
dependency at all." Asked whether to continue into A4; the user replied to
continue.
