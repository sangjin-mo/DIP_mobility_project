# Phase A4 — Image selection and payload construction

**Commit:** `13efc87` — "A4: image selection and payload construction"
**Date:** 2026-08-17 00:39

## What was built

- `models.py` gained a `Payload` model.
- `pipeline/select_images.py` — three-tier priority selection: anomaly
  exemplar → normal representative → undetermined exemplar, per spec §7.
  Enforces a **0.40 quality floor** (`IMAGE_QUALITY_MIN` in `config.py`) and
  does real Pillow-based resizing: **768px long edge**
  (`IMAGE_RESIZE_PX`), JPEG quality 85 (`IMAGE_JPEG_QUALITY`). Pillow was
  added as a dependency; `fake_vis.py` was updated to emit real, decodable
  JPEG placeholders instead of empty files, since the resize path needed
  something Pillow could actually open.
- `pipeline/payload.py` — implements spec §8's token-estimate formula
  (config-driven heuristic constants: `TOKEN_ESTIMATE_PER_ZONE=200`,
  `TOKEN_ESTIMATE_FIXED=300`, `TOKEN_ESTIMATE_SYSTEM_PROMPT=700`,
  `TOKEN_ESTIMATE_PER_IMAGE=765` — a heuristic, not real tokenization, since
  no tokenizer dependency exists and the real OpenAI SDK in A5 can only
  report actual token counts *after* a call completes) and the **3→2→1→0
  image-budget degradation ladder** to stay under budget, producing
  `payload.json`. `payload.json` carries everything `metadata.json` has
  minus the LLM output, plus obstruction counts and degradation notes that
  the LLM call and A6's regeneration path need but `metadata.json` doesn't
  carry.

## Two real bugs found through testing, not by inspection

1. **"Normal representative" median skew.** Computing the median 정상-count
   over *all* eligible images — including the image already claimed as the
   anomaly exemplar (whose count is 0) — skewed "typical" toward zero. A
   unit test caught this. Fix: exclude already-claimed images from the
   candidate pool used for the median.

2. **The atomic-swap data-loss bug (the significant one).**
   `copy_and_resize_images` wrote selected images directly to the *final*
   report path, but `write_report` performs an atomic
   temp-directory-then-rename swap. Writing images first and then calling
   `write_report` meant the swap silently wiped out the just-written images
   directory, because the swap always builds a fresh temp directory from
   scratch — the images weren't inside it. **This was not caught by any
   unit test** — only the real end-to-end smoke test (an actual server,
   actual files on disk) exposed it. Fixed by extending
   `storage/layout.py::write_report` to accept an `extra_writers`
   parameter, so image-copying and `payload.json`-writing join the *same*
   atomic swap as `report.md`/`metadata.json`. A regression test was added
   specifically to prove `extra_writers` content survives the swap.

   This exact bug class was hit **again, independently, in A6** — see that
   phase's notes.

## Verification

102 tests after A4, ruff clean, `contracts/validate.py` passes. Verified
end-to-end against a real running server, confirming `images/` +
`payload.json` + `report.md` + `metadata.json` all appear together
atomically.

## Checkpoint

Unlike A3, A4 had no natural stopping point — framed as "purely internal
plumbing with no new external-facing deliverable" — so the build continued
straight into A5.
