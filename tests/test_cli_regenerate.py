"""Tests for `cli.py`'s `regenerate` command — A6's acceptance criterion:
"`regenerate {patrol_id}` produces a fresh report from stored `payload.json`
with no rover or database dependency."

Every test here builds an original report through the real pipeline (no
`Store`/DB involved — segmentation/aggregation take already-loaded lists,
not a live database), then regenerates it with a *mocked* LLM client and
checks the result. `data_root` is deliberately never created in these
tests' `tmp_path` after the original build finishes — proving `_regenerate`
genuinely doesn't need it, not just that it wasn't asked to use it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_report.cli import _regenerate
from ai_report.config import get_settings
from ai_report.devtools.fake_rover import generate_patrol_plan
from ai_report.devtools.fake_vis import generate_analysis_results, write_analysis_files
from ai_report.llm.client import generate_report
from ai_report.pipeline.aggregate import aggregate
from ai_report.pipeline.payload import build_payload, write_payload
from ai_report.pipeline.segment import segment_patrol
from ai_report.pipeline.select_images import apply_image_selection, copy_and_resize_images
from ai_report.render.markdown import render_report
from ai_report.storage.layout import write_report

PATROL_ID = "20260813_1430"


def build_original_report(tmp_path: Path, seed: int = 1) -> Path:
    """Build a complete report through the real A1-A4 pipeline (no Store),
    returning its report directory. `tmp_path/data` only exists during this
    call — callers that want to prove regenerate needs no database access
    should `shutil.rmtree` it afterward (or never look at it again).
    """
    settings = get_settings()
    data_root = tmp_path / "data"
    report_root = tmp_path / "reports"

    plan = generate_patrol_plan(PATROL_ID, duration_s=300, num_zones=2, num_estops=1, seed=seed)
    results = generate_analysis_results(PATROL_ID, num_zones=2, images_per_zone=5, duration_s=300, seed=seed)
    write_analysis_files(results, data_root, PATROL_ID)

    seg = segment_patrol(PATROL_ID, plan.telemetry, plan.events, results, settings)
    agg = aggregate(seg, udp_received=len(plan.telemetry), udp_expected=len(plan.telemetry), settings=settings)
    agg = apply_image_selection(agg, seg, settings)
    payload, _tokens = build_payload(agg, seg, settings)
    md = render_report(agg, seg.obstruction_counts())

    return write_report(
        PATROL_ID, md, agg, report_root,
        extra_writers=[
            lambda tmp: copy_and_resize_images(agg, seg, data_root, tmp, settings),
            lambda tmp: write_payload(payload, tmp),
        ],
    )


def mock_llm_response(zone_ids: list[int], summary: str = "재생성된 요약") -> MagicMock:
    body = {
        "summary_ko": summary,
        "overall_note_ko": "재생성 종합 소견",
        "zones": [
            {
                "zone_id": zid,
                "growth_note_ko": f"{zid}구역 재생성 소견",
                "env_note_ko": "재생성 환경 소견",
                "visual_findings_ko": [],
                "recommended_actions_ko": [],
            }
            for zid in zone_ids
        ],
        "path_obstructions_ko": [],
        "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "재생성 제안",
    }
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(body, ensure_ascii=False)))]
    resp.usage = MagicMock(prompt_tokens=500, completion_tokens=150)
    return resp


def patched_generate_report(mock_client: MagicMock):
    """A drop-in replacement for `ai_report.cli.generate_report` that routes
    through the real `generate_report` logic but with a mocked OpenAI
    client — so retry/parsing/dropping behaviour is exercised for real,
    while no network call happens anywhere.
    """
    async def _fake(payload, images, valid_zone_ids, settings):
        return await generate_report(payload, images, valid_zone_ids, settings, client=mock_client)
    return _fake


async def test_regenerate_produces_a_different_report_with_no_data_root(tmp_path: Path):
    original_dir = build_original_report(tmp_path)
    original_text = (original_dir / "report.md").read_text(encoding="utf-8")
    zone_ids = [z["zone_id"] for z in json.loads((original_dir / "payload.json").read_text())["zones"]]

    import shutil
    shutil.rmtree(tmp_path / "data")  # prove regenerate doesn't need this at all

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_llm_response(zone_ids))

    with patch("ai_report.cli.generate_report", new=patched_generate_report(mock_client)):
        final_dir = await _regenerate(PATROL_ID, tmp_path / "reports", get_settings())

    new_text = (final_dir / "report.md").read_text(encoding="utf-8")
    assert new_text != original_text
    assert "재생성된 요약" in new_text


async def test_regenerate_carries_images_forward(tmp_path: Path):
    """Regression test for the bug an end-to-end smoke test found: images/
    must survive regeneration's atomic swap, not just the original build's.
    """
    original_dir = build_original_report(tmp_path)
    original_images = sorted(p.name for p in (original_dir / "images").iterdir())
    assert original_images  # sanity: the fixture actually selected images
    zone_ids = [z["zone_id"] for z in json.loads((original_dir / "payload.json").read_text())["zones"]]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_llm_response(zone_ids))

    with patch("ai_report.cli.generate_report", new=patched_generate_report(mock_client)):
        final_dir = await _regenerate(PATROL_ID, tmp_path / "reports", get_settings())

    regenerated_images = sorted(p.name for p in (final_dir / "images").iterdir())
    assert regenerated_images == original_images


async def test_regenerate_updates_metadata_llm_block(tmp_path: Path):
    original_dir = build_original_report(tmp_path)
    zone_ids = [z["zone_id"] for z in json.loads((original_dir / "payload.json").read_text())["zones"]]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_llm_response(zone_ids))

    with patch("ai_report.cli.generate_report", new=patched_generate_report(mock_client)):
        final_dir = await _regenerate(PATROL_ID, tmp_path / "reports", get_settings())

    metadata = json.loads((final_dir / "metadata.json").read_text())
    assert metadata["llm"]["enabled"] is True
    assert metadata["llm"]["input_tokens"] == 500
    assert metadata["llm"]["output_tokens"] == 150


async def test_regenerate_preserves_deterministic_figures(tmp_path: Path):
    """Regeneration re-runs the LLM but must not change any number — those
    all came from the original payload.json, not from the new LLM call.
    """
    original_dir = build_original_report(tmp_path)
    original_metadata = json.loads((original_dir / "metadata.json").read_text())
    zone_ids = [z["zone_id"] for z in json.loads((original_dir / "payload.json").read_text())["zones"]]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_llm_response(zone_ids))

    with patch("ai_report.cli.generate_report", new=patched_generate_report(mock_client)):
        final_dir = await _regenerate(PATROL_ID, tmp_path / "reports", get_settings())

    new_metadata = json.loads((final_dir / "metadata.json").read_text())
    assert new_metadata["overall_status"] == original_metadata["overall_status"]
    assert new_metadata["data_completeness"] == original_metadata["data_completeness"]
    assert new_metadata["zones"] == original_metadata["zones"]


async def test_regenerate_falls_back_gracefully_when_llm_fails(tmp_path: Path):
    """A6 acceptance: fallback report still has all six sections and llm.enabled: false."""
    build_original_report(tmp_path)

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("simulated total failure"))

    with patch("ai_report.cli.generate_report", new=patched_generate_report(mock_client)):
        final_dir = await _regenerate(PATROL_ID, tmp_path / "reports", get_settings())

    metadata = json.loads((final_dir / "metadata.json").read_text())
    assert metadata["llm"]["enabled"] is False

    report_md = (final_dir / "report.md").read_text(encoding="utf-8")
    sections = [line for line in report_md.splitlines() if line.startswith("## ")]
    assert sections == [
        "## 순찰 요약", "## 구역별 생육 현황", "## 환경 조건",
        "## 통로 장애 요인", "## 권장 조치", "## 데이터 한계",
    ]


async def test_regenerate_missing_payload_raises_clear_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await _regenerate("nonexistent_patrol", tmp_path / "reports", get_settings())
