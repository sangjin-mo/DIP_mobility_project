import json

import pytest

from ai_report.models import (
    DataCompleteness,
    LlmMetadata,
    PatrolAggregate,
    ReportStatus,
)
from web_dashboard.services.report_service import InvalidReportError, ReportService


def test_report_service_reads_existing_metadata_shape(tmp_path):
    patrol_id = "20260818_1430"
    report_dir = tmp_path / patrol_id
    report_dir.mkdir()
    aggregate = PatrolAggregate(
        patrol_id=patrol_id,
        patrol_date="2026-08-18",
        generated_at="2026-08-18T14:31:00+09:00",
        duration_min=12,
        overall_status=ReportStatus.NORMAL,
        llm=LlmMetadata(enabled=False),
        data_completeness=DataCompleteness(
            udp_received=10,
            udp_expected=10,
            rate=1.0,
            images_analysed=0,
            zone_boundary_confidence="high",
        ),
        zones=[],
    )
    (report_dir / "metadata.json").write_text(
        json.dumps(aggregate.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    (report_dir / "report.md").write_text("# test", encoding="utf-8")

    service = ReportService(tmp_path)

    assert service.list_reports()[0]["patrol_id"] == patrol_id
    assert service.markdown(patrol_id) == "# test"


def test_report_service_rejects_invalid_patrol_id(tmp_path):
    service = ReportService(tmp_path)

    with pytest.raises(InvalidReportError):
        service.metadata("../outside")
