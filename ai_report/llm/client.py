"""⑤ LLM call — spec §9. The only stage in this whole subsystem that
touches the network (GUIDELINES.md hard rule 2 / spec §1's architecture
diagram: every other stage is green/deterministic).

`generate_report` never raises and never aborts a report (spec §12's error
matrix: "LLM final failure -> Fallback report, `llm.enabled = false`").
Every failure path — a 400, exhausted retries, a schema-invalid response,
an unexpected exception — returns `(None, LlmMetadata(enabled=False))`
rather than propagating, so the caller can always fall back to rendering
without LLM content.

Called by: whatever runs the pipeline for a patrol, immediately after
`pipeline/payload.py::build_payload` and `pipeline/select_images.py::load_selected_images`
— currently only `tests/test_llm_client.py`, since production orchestration
doesn't exist yet. `generate_report` accepts an injected `client` so tests
never construct a real `AsyncOpenAI` (GUIDELINES.md: "No network calls in any test").
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging

from openai import (
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from pydantic import ValidationError

from ai_report.config import Settings
from ai_report.llm.prompts import SYSTEM_PROMPT
from ai_report.llm.schema import LlmReportOutput, output_json_schema
from ai_report.models import LlmMetadata, Payload

logger = logging.getLogger(__name__)

# The prompt's own 금지 규칙 (spec §9.2) asks the model not to use these —
# this is the defensive runtime backstop, not the enforcement mechanism.
# Real models don't follow instructions with 100% reliability; a violation
# here is logged, not silently trusted away.
_PROHIBITED_CAUSAL_PHRASES = ("때문에", "로 인해", "원인은", "영향으로")
_PROHIBITED_COUNT_PHRASES = ("개체 수", "그루", "포기")

_RETRYABLE_EXCEPTIONS = (APITimeoutError, RateLimitError, InternalServerError)


def _build_messages(payload: Payload, images: dict[str, bytes]) -> list[dict]:
    """System + user messages for the API call.

    The user message is the payload as JSON text, followed by one
    `image_url` content block per selected image, base64-encoded as a
    `data:` URI (no image hosting involved — everything is local files).
    Called only by `generate_report`.
    """
    payload_json = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
    content: list[dict] = [{"type": "text", "text": payload_json}]
    for data in images.values():
        b64 = base64.b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _compute_cost_usd(input_tokens: int, output_tokens: int, settings: Settings) -> float:
    """Spec §8's cost formula: per-1M-token rates from `config.py`, not hardcoded here."""
    return (
        (input_tokens / 1_000_000) * settings.LLM_INPUT_COST_PER_1M_USD
        + (output_tokens / 1_000_000) * settings.LLM_OUTPUT_COST_PER_1M_USD
    )


def _scan_prohibited_language(output: LlmReportOutput) -> list[str]:
    """Every `_ko` string field in `output`, checked against the two banned-phrase
    lists. Returns one description per violation found (empty if clean).

    Called by `generate_report` after a successful, schema-valid response —
    a violation is logged as a warning, not treated as a failure (rejecting
    an otherwise-usable report over one stray phrase would be worse than
    the phrase itself). Directly exercised by
    `tests/test_llm_client.py`'s adversarial-fixture tests, since real
    model compliance can't be tested without a live call.
    """
    texts = [output.summary_ko, output.overall_note_ko, output.next_patrol_suggestion_ko]
    texts.extend(output.path_obstructions_ko)
    texts.extend(output.data_limitations_ko)
    for zone in output.zones:
        texts.append(zone.growth_note_ko)
        texts.append(zone.env_note_ko)
        texts.extend(zone.visual_findings_ko)
        texts.extend(zone.recommended_actions_ko)

    violations: list[str] = []
    for text in texts:
        for phrase in _PROHIBITED_CAUSAL_PHRASES:
            if phrase in text:
                violations.append(f"causal language {phrase!r} in {text!r}")
        for phrase in _PROHIBITED_COUNT_PHRASES:
            if phrase in text:
                violations.append(f"plant-count language {phrase!r} in {text!r}")
    return violations


def _drop_unknown_zones(output: LlmReportOutput, valid_zone_ids: set[int]) -> LlmReportOutput:
    """Remove any `ZoneNote` whose `zone_id` isn't in `valid_zone_ids`, logging each drop.

    Spec §9: "If a returned zone_id is not in the aggregate, drop it and
    log." Called by `generate_report` after schema validation succeeds.
    """
    kept = []
    for zone in output.zones:
        if zone.zone_id in valid_zone_ids:
            kept.append(zone)
        else:
            logger.warning("LLM returned unknown zone_id=%s; dropping", zone.zone_id)
    return output.model_copy(update={"zones": kept})


async def _call_with_retry(
    client: AsyncOpenAI, messages: list[dict], schema: dict, settings: Settings
):
    """The actual `chat.completions.create` call, retried per spec §9.

    3 retries beyond the first attempt (4 total), backoff 2/4/8s before
    each retry, on `APITimeoutError`/`RateLimitError`/`InternalServerError`
    (timeout, 429, 5xx). `BadRequestError` (400) and anything else
    unexpected propagate immediately, no retry — spec §9: "Do not retry on
    400." Returns the raw API response on success. Called only by
    `generate_report`, inside its own broad exception guard.
    """
    attempts = 1 + settings.LLM_MAX_RETRIES
    for attempt in range(attempts):
        try:
            return await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "patrol_report", "schema": schema, "strict": True},
                },
            )
        except _RETRYABLE_EXCEPTIONS as exc:
            if attempt == attempts - 1:
                raise
            backoff_s = settings.LLM_RETRY_BACKOFF_BASE_S * (2**attempt)
            logger.warning(
                "LLM call failed (%s), retrying in %.0fs (attempt %d/%d)",
                exc, backoff_s, attempt + 1, attempts,
            )
            await asyncio.sleep(backoff_s)
    raise AssertionError("unreachable: loop always returns or raises")


async def generate_report(
    payload: Payload,
    images: dict[str, bytes],
    valid_zone_ids: set[int],
    settings: Settings,
    client: AsyncOpenAI | None = None,
) -> tuple[LlmReportOutput | None, LlmMetadata]:
    """Call the LLM for one patrol's prose, or fail gracefully to `(None, LlmMetadata(enabled=False))`.

    `valid_zone_ids` should be `{z.zone_id for z in agg.zones}` — used by
    `_drop_unknown_zones`. `client` is normally `None` in production (a
    real `AsyncOpenAI` is constructed from `settings.OPENAI_API_KEY`); every
    test passes a fake/mocked client instead, so no test call ever reaches
    the network.

    On success, records `input_tokens`/`output_tokens` from the API
    response's own usage figures (not the pre-call estimate from
    `pipeline/payload.py::estimate_tokens`, which is a heuristic for
    budget-checking, not a substitute for the real count) and computes
    `cost_usd` from `config.py`'s per-1M rates.

    Every failure mode — `LLM_ENABLED=False`, a 400, retries exhausted, a
    schema-invalid response body, or any other unexpected exception —
    returns `(None, LlmMetadata(enabled=False))` rather than raising. This
    function is the fallback boundary spec §12 describes: whatever goes
    wrong here, the report still gets rendered, just without LLM content.
    """
    if not settings.LLM_ENABLED:
        return None, LlmMetadata(enabled=False)

    own_client = client is None
    if own_client:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.LLM_TIMEOUT_S)

    try:
        messages = _build_messages(payload, images)
        schema = output_json_schema()

        try:
            response = await _call_with_retry(client, messages, schema, settings)
        except BadRequestError:
            logger.exception("LLM call failed with 400 Bad Request; not retrying")
            return None, LlmMetadata(enabled=False)
        except _RETRYABLE_EXCEPTIONS:
            logger.exception("LLM call failed after exhausting all retries")
            return None, LlmMetadata(enabled=False)

        try:
            output = LlmReportOutput.model_validate_json(response.choices[0].message.content)
        except ValidationError:
            logger.exception("LLM response failed structured-output schema validation")
            return None, LlmMetadata(enabled=False)

        for violation in _scan_prohibited_language(output):
            logger.warning("LLM prompt-prohibition violation: %s", violation)

        output = _drop_unknown_zones(output, valid_zone_ids)

        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        metadata = LlmMetadata(
            enabled=True,
            model=settings.LLM_MODEL,
            prompt_version=settings.PROMPT_VERSION,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=_compute_cost_usd(input_tokens, output_tokens, settings),
        )
        return output, metadata

    except Exception:
        # Final safety net: spec §12 says the report must never abort
        # because of this stage, regardless of what goes wrong.
        logger.exception("LLM call failed unexpectedly; falling back")
        return None, LlmMetadata(enabled=False)
    finally:
        if own_client:
            await client.close()
