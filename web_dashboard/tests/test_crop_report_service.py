import json

from ai_report.models import (
    DataCompleteness,
    LlmMetadata,
    PatrolAggregate,
    ReportStatus,
    ZoneEnv,
    ZoneMetadata,
)
from web_dashboard.services.crop_report_service import CropReportService
from web_dashboard.services.report_service import ReportService


def test_latest_crop_report_combines_metadata_and_llm_markdown(tmp_path):
    patrol_id = "20260821_1430"
    report_dir = tmp_path / patrol_id
    report_dir.mkdir()
    aggregate = PatrolAggregate(
        patrol_id=patrol_id,
        patrol_date="2026-08-21",
        generated_at="2026-08-21T14:35:00+09:00",
        duration_min=5,
        overall_status=ReportStatus.CAUTION,
        llm=LlmMetadata(enabled=True, model="test-model"),
        data_completeness=DataCompleteness(
            udp_received=10,
            udp_expected=10,
            rate=1.0,
            images_analysed=1,
            zone_boundary_confidence="high",
        ),
        zones=[
            ZoneMetadata(
                zone_id=1,
                zone_name="토마토",
                status=ReportStatus.CAUTION,
                env=ZoneEnv(),
                observations={"토마토": {"정상": 3, "병충해_의심": 1}},
                confidence="high",
            )
        ],
    )
    (report_dir / "metadata.json").write_text(
        json.dumps(aggregate.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
    )
    (report_dir / "report.md").write_text("# LLM 작물 레포트", encoding="utf-8")

    result = CropReportService(ReportService(tmp_path)).latest()

    assert result["available"] is True
    assert result["llm_enabled"] is True
    assert result["zones"][0]["label"] == "A"
    assert result["zones"][0]["zone_name"] == "토마토"
    assert result["report_markdown"] == "# LLM 작물 레포트"

    selected = CropReportService(ReportService(tmp_path)).get(patrol_id)
    assert selected["patrol_id"] == patrol_id
    assert selected["report_markdown"] == "# LLM 작물 레포트"


def test_latest_crop_report_has_explicit_empty_state(tmp_path):
    result = CropReportService(ReportService(tmp_path)).latest()

    assert result["available"] is False
    assert result["zones"] == []
