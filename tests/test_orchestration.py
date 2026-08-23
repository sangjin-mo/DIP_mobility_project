"""Tests for `orchestration.py::run_patrol_pipeline` — the piece
`CALL_MAP.md` documented as "not wired up yet": the automatic A2-A5 chain
triggered by `PATROL_END`, as opposed to `cli.py regenerate` (A6), which
only rebuilds a report that already exists.

Every test here populates a real `Store` the way ingest actually would
(`store.insert_telemetry`/`insert_event`, one row per `fake_rover` packet/
event) and writes real analysis JSON + placeholder JPEGs via `fake_vis`,
then calls `run_patrol_pipeline` directly against that store — this is the
one thing `test_cli_regenerate.py`'s `build_original_report` deliberately
does *not* do (it hands segmentation already-loaded lists, bypassing
`Store` entirely, since A6 has no database access at all). The LLM client
is always mocked (GUIDELINES.md: "No network calls in any test").
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from ai_report.config import Settings
from ai_report.devtools.fake_rover import generate_patrol_plan
from ai_report.devtools.fake_vis import generate_analysis_results, write_analysis_files
from ai_report.ingest.vis_watcher import VisWatcher
from ai_report.orchestration import run_patrol_pipeline

PATROL_ID = "20260813_1430"


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        DATA_ROOT=tmp_path / "data",
        REPORT_ROOT=tmp_path / "reports",
        VIS_COMPLETE_TIMEOUT_S=overrides.pop("VIS_COMPLETE_TIMEOUT_S", 5),
        VIS_WATCHER_POLL_INTERVAL_S=overrides.pop("VIS_WATCHER_POLL_INTERVAL_S", 0.05),
        **overrides,
    )


def _populate_store(store, seed: int = 1, num_zones: int = 2, duration_s: int = 300) -> list[int]:
    """Insert fake telemetry/events into `store`, the way real ingest would.

    Returns the zone_ids fake_vis will also generate images for, so callers
    can build a matching mocked LLM response.
    """
    plan = generate_patrol_plan(PATROL_ID, duration_s=duration_s, num_zones=num_zones, num_estops=1, seed=seed)
    for pkt in plan.telemetry:
        store.insert_telemetry(pkt)
    for evt in plan.events:
        store.insert_event(evt)
    return list(range(1, num_zones + 1))


def _write_vis_results(data_root: Path, zone_ids: list[int], complete: bool = True, seed: int = 1) -> None:
    results = generate_analysis_results(
        PATROL_ID, num_zones=len(zone_ids), images_per_zone=5, duration_s=300, seed=seed
    )
    write_analysis_files(results, data_root, PATROL_ID, write_complete=complete)


def _mock_llm_response(zone_ids: list[int], summary: str = "자동 생성 요약") -> MagicMock:
    body = {
        "summary_ko": summary,
        "overall_note_ko": "자동 생성 종합 소견",
        "zones": [
            {
                "zone_id": zid,
                "growth_note_ko": f"{zid}구역 소견",
                "env_note_ko": "환경 소견",
                "visual_findings_ko": [],
                "recommended_actions_ko": [],
            }
            for zid in zone_ids
        ],
        "path_obstructions_ko": [],
        "data_limitations_ko": [],
        "next_patrol_suggestion_ko": "제안",
    }
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(body, ensure_ascii=False)))]
    resp.usage = MagicMock(prompt_tokens=400, completion_tokens=100)
    return resp


def _mock_client(zone_ids: list[int]) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_llm_response(zone_ids))
    client.close = AsyncMock()
    return client


async def test_full_pipeline_writes_a_complete_report(tmp_path: Path, store):
    """ADR-0009: zones are grouped by classified crop type, not by the
    fake-rover "zone" concept `_populate_store`/`_write_vis_results` still
    use to spread fixture data around -- `fake_vis.generate_analysis_results`
    always classifies as `class: "tomato"` regardless of which fake zone it
    was generated for, so all of it collapses into exactly one crop-type
    zone here.
    """
    settings = _settings(tmp_path)
    zone_ids = _populate_store(store)
    _write_vis_results(settings.DATA_ROOT, zone_ids)

    report_dir = await run_patrol_pipeline(PATROL_ID, store, settings, llm_client=_mock_client([1]))

    assert report_dir is not None
    assert (report_dir / "report.md").is_file()
    assert (report_dir / "metadata.json").is_file()
    assert (report_dir / "payload.json").is_file()

    metadata = json.loads((report_dir / "metadata.json").read_text())
    assert metadata["llm"]["enabled"] is True
    assert metadata["llm"]["input_tokens"] == 400
    assert len(metadata["zones"]) == 1
    assert metadata["zones"][0]["zone_name"] == "토마토구역"

    images = sorted(p.name for p in (report_dir / "images").iterdir())
    assert images  # at least one image was selected and resized


async def test_llm_failure_still_produces_a_fallback_report(tmp_path: Path, store):
    """Spec §12: 'LLM final failure -> Fallback report, llm.enabled = false.'"""
    settings = _settings(tmp_path)
    zone_ids = _populate_store(store)
    _write_vis_results(settings.DATA_ROOT, zone_ids)

    failing_client = MagicMock()
    failing_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("simulated outage"))
    failing_client.close = AsyncMock()

    report_dir = await run_patrol_pipeline(PATROL_ID, store, settings, llm_client=failing_client)

    assert report_dir is not None
    metadata = json.loads((report_dir / "metadata.json").read_text())
    assert metadata["llm"]["enabled"] is False
    sections = [
        line for line in (report_dir / "report.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    assert sections == [
        "## 순찰 요약", "## 구역별 생육 현황", "## 환경 조건",
        "## 통로 장애 요인", "## 권장 조치", "## 데이터 한계",
    ]


async def test_proceeds_without_vis_complete_after_timeout(tmp_path: Path, store):
    """Spec §12: '_COMPLETE never written -> Timeout 600s -> Proceed with
    available analyses, note the gap.' Uses a short timeout (see _settings)
    so the test doesn't actually wait 600s.
    """
    settings = _settings(tmp_path, VIS_COMPLETE_TIMEOUT_S=1)
    zone_ids = _populate_store(store)
    _write_vis_results(settings.DATA_ROOT, zone_ids, complete=False)  # no _COMPLETE marker

    # ADR-0009: all fake_vis detections classify as "tomato" -> one crop-type zone.
    report_dir = await run_patrol_pipeline(PATROL_ID, store, settings, llm_client=_mock_client([1]))

    assert report_dir is not None  # did not hang or crash waiting for _COMPLETE
    metadata = json.loads((report_dir / "metadata.json").read_text())
    assert metadata["data_completeness"]["images_analysed"] > 0  # still used what VIS had written


async def test_unknown_vis_state_is_caught_and_returns_none(tmp_path: Path, store, monkeypatch):
    """`vis_watcher.py`'s module docstring: an unknown VIS `state` is a
    contract violation `scan_once` lets raise, not silently drop
    (GUIDELINES.md hard rule / spec §12). `run_patrol_pipeline` must catch
    that at its boundary so one bad patrol never crashes the long-running
    `serve` process — see its own docstring's "Never raises" paragraph.
    """
    settings = _settings(tmp_path)
    _populate_store(store)

    def _raise(self, patrol_id):
        raise ValueError("unknown VIS state — contract violation")

    monkeypatch.setattr(VisWatcher, "scan_once", _raise)

    report_dir = await run_patrol_pipeline(PATROL_ID, store, settings, llm_client=_mock_client([1, 2]))

    assert report_dir is None


async def test_no_report_when_patrol_has_no_data_at_all(tmp_path: Path, store):
    """An empty/unknown patrol_id must not crash the caller either — it
    should just produce a text-only, zero-zone report rather than raising,
    same as `pipeline/aggregate.py::aggregate`'s documented empty-input
    behaviour (`overall_status` defaults to 정상 with no zones).
    """
    settings = _settings(tmp_path, VIS_COMPLETE_TIMEOUT_S=1)

    report_dir = await run_patrol_pipeline("20260101_0000", store, settings, llm_client=_mock_client([]))

    assert report_dir is not None
    metadata = json.loads((report_dir / "metadata.json").read_text())
    assert metadata["zones"] == []
