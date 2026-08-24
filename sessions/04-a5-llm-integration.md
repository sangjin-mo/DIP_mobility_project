# Phase A5 — LLM integration

**Commit:** `be8967d` — "A5: LLM integration"
**Date:** 2026-08-17 01:10

Framed at the time as "the biggest phase yet" — it required the real LLM
output schema (replacing A3's placeholder shape) and a rework, not just an
extension, of `render/markdown.py`.

## What was built

- Added the `openai` dependency. **Explicit version-pinning decision:**
  GUIDELINES.md pins "OpenAI Python SDK v1.x," but the environment resolved
  a newer default major version. Since the newer version's API surface
  couldn't be verified against known behavior, and the instruction was
  explicit, `pyproject.toml` pins `openai>=1.30,<2.0` rather than guessing
  compatibility. The pin is commented in `pyproject.toml` pointing back at
  GUIDELINES.md.
- `llm/schema.py` — the real structured-output schema per spec §9:
  `summary_ko`, `overall_note_ko`, per-zone notes, `path_obstructions_ko`,
  `data_limitations_ko`, `next_patrol_suggestion_ko`. Verified strict-schema
  properties directly (`additionalProperties: false`, a complete `required`
  list at every level, including the nested `ZoneNote` definition).
  **No numeric field exists anywhere in this schema except `zone_id`**,
  which only identifies which zone a note belongs to — hard rule 1 ("the
  LLM never computes numbers") is enforced here as a schema-shape guarantee,
  not just a runtime check.
- `llm/prompts.py` — the system prompt, stored verbatim. This becomes
  load-bearing later, in A6's hash-pinning mechanism.
- `pipeline/select_images.py` gained `load_selected_images()`, returning
  resized image bytes in memory for the API call — independent of
  `copy_and_resize_images`'s write-to-disk timing via `write_report`'s
  `extra_writers`, since the LLM call has to happen *before* rendering, not
  as part of the atomic storage swap.
- Checked the actual `openai` v1.x SDK's exception signatures before writing
  the client, so both the `except` clauses and the test mocks would be
  correct against real behavior rather than assumed behavior.
- `llm/client.py` — the real call:
  - **Retry/backoff:** 4 total attempts, exponential backoff 2s/4s/8s
    (`LLM_RETRY_BACKOFF_BASE_S = 2.0` in config; backoff before retry *i* is
    `base × 2^(i-1)`) on timeout/429/5xx — matches spec §9's "3 retries,
    exponential backoff 2/4/8s" read as 3 retries beyond the first attempt,
    4 attempts total.
  - **HTTP 400 explicitly skips retry entirely** — a malformed request won't
    succeed on retry.
  - Cost metering from config-driven per-1M-token rates
    (`LLM_INPUT_COST_PER_1M_USD = 0.20`, `LLM_OUTPUT_COST_PER_1M_USD = 1.20`,
    model `gpt-5.6-luna`).
  - Unknown `zone_id` values returned by the model are silently dropped and
    logged — verified with a smoke test that deliberately injected
    `zone_id=99` and confirmed it never appeared in rendered output, since
    `render_report` only looks up LLM zone notes by real aggregate zone IDs.
  - A defensive prohibition-language scanner backs up the system prompt's
    own content rules, as a backstop rather than the sole guard.
  - **Every failure path falls back to `LlmMetadata(enabled=False)` rather
    than aborting the report** — consistent with hard rule 2 (a full report
    must be producible with the LLM disabled) and the spec's error matrix.
- `render/markdown.py` was **reworked, not just extended** — the A3-era
  placeholder shape never matched spec §9's real output. LLM prose is always
  additive next to deterministic figures, never a replacement: every
  section's deterministic fallback still renders whether or not the LLM
  actually ran.

## Verification

All tests mock the OpenAI client — no test or smoke script anywhere in this
repo makes a real API call. Verified end-to-end with a mocked client against
a real running server, producing a complete `report.md` with prose, a
`metadata.json` with real cost/token figures, and `payload.json`/`images/`
all appearing atomically together. 129 tests total, ruff clean, contracts
validate.

## Checkpoint

Retry/backoff and fallback-report behavior for A6 was already substantially
covered by this phase's failure handling (the logic lives in `llm/client.py`
regardless of caller), but `cli.py regenerate` and prompt-version-drift
enforcement were still open. Continued straight into A6.
