from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import BadRequestError, RateLimitError

from ai_report.config import get_settings
from ai_report.llm.client import _scan_prohibited_language, generate_report
from ai_report.llm.schema import LlmReportOutput
from ai_report.models import DataCompleteness, Payload, ReportStatus

PATROL_ID = "20260813_1430"


def make_payload(zones=None) -> Payload:
    return Payload(
        patrol_id=PATROL_ID, patrol_date="2026-08-13", duration_min=18, overall_status=ReportStatus.NORMAL,
        data_completeness=DataCompleteness(udp_received=100, udp_expected=100, rate=1.0, images_analysed=0, zone_boundary_confidence="high"),
        zones=zones or [], obstructions={}, known_limitations=[], prompt_version="v1.0",
    )


def valid_output_dict(zone_ids: list[int] | None = None) -> dict:
    zone_ids = zone_ids if zone_ids is not None else [1]
    return {
        "summary_ko": "요약입니다.",
        "overall_note_ko": "종합 소견입니다.",
        "zones": [
            {
                "zone_id": zid,
                "growth_note_ko": "생육 소견",
                "env_note_ko": "환경 소견",
                "visual_findings_ko": ["소견1"],
                "recommended_actions_ko": [],
            }
            for zid in zone_ids
        ],
        "path_obstructions_ko": [],
        "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "다음 순찰 제안",
    }


def mock_response(content: dict | str, prompt_tokens: int = 1000, completion_tokens: int = 200):
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return resp


def mock_client(**create_kwargs) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(**create_kwargs)
    return client


def rate_limit_error() -> RateLimitError:
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return RateLimitError("rate limited", response=resp, body=None)


def bad_request_error() -> BadRequestError:
    resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))
    return BadRequestError("bad request", response=resp, body=None)


async def test_llm_disabled_returns_none_without_calling_client():
    settings = get_settings().model_copy(update={"LLM_ENABLED": False})
    client = mock_client()
    output, metadata = await generate_report(make_payload(), {}, valid_zone_ids=set(), settings=settings, client=client)
    assert output is None
    assert metadata.enabled is False
    client.chat.completions.create.assert_not_called()


async def test_happy_path_returns_output_and_metadata():
    settings = get_settings()
    client = mock_client(return_value=mock_response(valid_output_dict()))
    output, metadata = await generate_report(make_payload(), {}, valid_zone_ids={1}, settings=settings, client=client)
    assert output.summary_ko == "요약입니다."
    assert metadata.enabled is True
    assert metadata.model == settings.LLM_MODEL
    assert metadata.prompt_version == settings.PROMPT_VERSION
    assert metadata.input_tokens == 1000
    assert metadata.output_tokens == 200
    assert metadata.cost_usd == pytest.approx(1000 / 1e6 * settings.LLM_INPUT_COST_PER_1M_USD + 200 / 1e6 * settings.LLM_OUTPUT_COST_PER_1M_USD)


async def test_retries_on_rate_limit_then_succeeds():
    settings = get_settings()
    client = mock_client(side_effect=[rate_limit_error(), rate_limit_error(), mock_response(valid_output_dict())])
    with patch("ai_report.llm.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        output, metadata = await generate_report(make_payload(), {}, valid_zone_ids={1}, settings=settings, client=client)
    assert output is not None
    assert metadata.enabled is True
    assert client.chat.completions.create.call_count == 3
    assert [c.args[0] for c in mock_sleep.call_args_list] == [2.0, 4.0]


async def test_retries_exhausted_falls_back_gracefully():
    settings = get_settings()
    client = mock_client(side_effect=rate_limit_error())
    with patch("ai_report.llm.client.asyncio.sleep", new=AsyncMock()):
        output, metadata = await generate_report(make_payload(), {}, valid_zone_ids=set(), settings=settings, client=client)
    assert output is None
    assert metadata.enabled is False
    assert client.chat.completions.create.call_count == 1 + settings.LLM_MAX_RETRIES


async def test_no_retry_on_bad_request():
    settings = get_settings()
    client = mock_client(side_effect=bad_request_error())
    output, metadata = await generate_report(make_payload(), {}, valid_zone_ids=set(), settings=settings, client=client)
    assert output is None
    assert metadata.enabled is False
    assert client.chat.completions.create.call_count == 1  # no retry at all


async def test_schema_invalid_response_falls_back_gracefully():
    settings = get_settings()
    client = mock_client(return_value=mock_response("not valid json at all"))
    output, metadata = await generate_report(make_payload(), {}, valid_zone_ids=set(), settings=settings, client=client)
    assert output is None
    assert metadata.enabled is False


async def test_unexpected_exception_never_propagates():
    settings = get_settings()
    client = mock_client(side_effect=ValueError("something totally unexpected"))
    output, metadata = await generate_report(make_payload(), {}, valid_zone_ids=set(), settings=settings, client=client)
    assert output is None
    assert metadata.enabled is False


async def test_unknown_zone_id_dropped_and_logged(caplog: pytest.LogCaptureFixture):
    settings = get_settings()
    client = mock_client(return_value=mock_response(valid_output_dict(zone_ids=[1, 99])))
    with caplog.at_level(logging.WARNING):
        output, _metadata = await generate_report(make_payload(), {}, valid_zone_ids={1}, settings=settings, client=client)
    assert [z.zone_id for z in output.zones] == [1]  # zone 99 dropped
    assert any("zone_id=99" in record.message for record in caplog.records)


def test_scan_prohibited_language_detects_causal_connectors():
    output = LlmReportOutput.model_validate({
        "summary_ko": "병충해가 확산되었습니다.",
        "overall_note_ko": "높은 습도 때문에 병충해가 증가했습니다.",
        "zones": [],
        "path_obstructions_ko": [], "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "권장 없음",
    })
    violations = _scan_prohibited_language(output)
    assert any("때문에" in v for v in violations)


def test_scan_prohibited_language_detects_plant_count_words():
    output = LlmReportOutput.model_validate({
        "summary_ko": "정상 개체 수 12그루가 관측되었습니다.",
        "overall_note_ko": "양호",
        "zones": [], "path_obstructions_ko": [], "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "없음",
    })
    violations = _scan_prohibited_language(output)
    assert any("개체 수" in v or "그루" in v for v in violations)


def test_scan_prohibited_language_clean_text_has_no_violations():
    output = LlmReportOutput.model_validate({
        "summary_ko": "정상 관측 수가 우세하게 확인되었습니다.",
        "overall_note_ko": "병충해_의심 개체가 동일 구역에서 함께 관찰되었습니다.",
        "zones": [], "path_obstructions_ko": [], "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "다음 순찰도 동일 경로를 권장합니다.",
    })
    assert _scan_prohibited_language(output) == []


async def test_undetermined_zone_gets_recapture_note_not_diagnosis_when_mocked_correctly():
    """Adversarial-fixture style test: a zone flagged for high undetermined_rate.
    We can't test that a *real* model chooses to follow the rule (no network
    calls allowed), but we can prove the pipeline correctly threads a
    recapture-flavoured mocked response through without mangling it, and
    that our own reconciliation logic doesn't second-guess it.
    """
    settings = get_settings()
    zone_note = valid_output_dict(zone_ids=[1])
    zone_note["zones"][0]["growth_note_ko"] = "판단불가 비율이 높아 생육 상태를 판단할 수 없어 재촬영이 필요합니다."
    zone_note["zones"][0]["recommended_actions_ko"] = ["재촬영 필요"]
    client = mock_client(return_value=mock_response(zone_note))
    output, _metadata = await generate_report(make_payload(), {}, valid_zone_ids={1}, settings=settings, client=client)
    assert "재촬영" in output.zones[0].growth_note_ko
    assert _scan_prohibited_language(output) == []
