from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_report.models import DataCompleteness, LlmMetadata, PatrolAggregate, ReportStatus
from ai_report.storage import layout
from ai_report.storage.layout import write_report

PATROL_ID = "20260813_1430"


def make_aggregate() -> PatrolAggregate:
    return PatrolAggregate(
        patrol_id=PATROL_ID,
        patrol_date="2026-08-13",
        duration_min=18,
        overall_status=ReportStatus.NORMAL,
        llm=LlmMetadata(enabled=False),
        data_completeness=DataCompleteness(
            udp_received=100, udp_expected=100, rate=1.0, images_analysed=0, zone_boundary_confidence="high"
        ),
        zones=[],
    )


def test_write_report_creates_final_directory_with_both_files(tmp_path: Path):
    final = write_report(PATROL_ID, "# report\n", make_aggregate(), tmp_path)
    assert final == tmp_path / PATROL_ID
    assert (final / "report.md").read_text(encoding="utf-8") == "# report\n"
    assert (final / "metadata.json").exists()


def test_write_report_metadata_is_valid_json_matching_the_aggregate(tmp_path: Path):
    agg = make_aggregate()
    final = write_report(PATROL_ID, "# report\n", agg, tmp_path)
    data = json.loads((final / "metadata.json").read_text(encoding="utf-8"))
    assert data["patrol_id"] == PATROL_ID
    assert data["overall_status"] == "정상"  # enum .value, not the Python repr
    assert "generated_at" in data  # stamped by write_report, not present on the model itself


def test_write_report_leaves_no_tmp_or_old_directories_behind(tmp_path: Path):
    write_report(PATROL_ID, "# v1\n", make_aggregate(), tmp_path)
    write_report(PATROL_ID, "# v2\n", make_aggregate(), tmp_path)  # regeneration path
    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {PATROL_ID}


def test_regeneration_overwrites_previous_report_content(tmp_path: Path):
    write_report(PATROL_ID, "# v1\n", make_aggregate(), tmp_path)
    final = write_report(PATROL_ID, "# v2\n", make_aggregate(), tmp_path)
    assert (final / "report.md").read_text(encoding="utf-8") == "# v2\n"


def test_a_failed_write_does_not_touch_an_existing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_report(PATROL_ID, "# good\n", make_aggregate(), tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(layout, "_write_files", boom)
    with pytest.raises(OSError, match="disk full"):
        write_report(PATROL_ID, "# bad\n", make_aggregate(), tmp_path)

    # the previous good report must still be there, untouched
    final = tmp_path / PATROL_ID
    assert (final / "report.md").read_text(encoding="utf-8") == "# good\n"
    # and no leftover tmp/old directories from the failed attempt
    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {PATROL_ID}


def test_no_partial_directory_is_ever_observable_at_the_final_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The core A3 acceptance criterion: WEB polling `{report_root}/{patrol_id}/`
    must never see a directory with only some of its files written.
    """
    real_write_files = layout._write_files

    def write_then_crash(tmp_dir, report_md, metadata):
        real_write_files(tmp_dir, report_md, metadata)  # report.md written
        raise OSError("crash after partial write")  # metadata.json never gets here

    monkeypatch.setattr(layout, "_write_files", write_then_crash)
    with pytest.raises(OSError):
        write_report(PATROL_ID, "# report\n", make_aggregate(), tmp_path)

    # nothing at all at the final path — a half-built tmp dir was never renamed into place
    assert not (tmp_path / PATROL_ID).exists()
    assert list(tmp_path.iterdir()) == []


def test_write_report_succeeds_for_a_patrol_with_no_zones(tmp_path: Path):
    """A3 acceptance: report generates successfully for a patrol with zero
    zones (the zero-images case reduces to this, since A4's image
    selection doesn't exist yet and no report content depends on images).
    """
    final = write_report(PATROL_ID, "# report\n", make_aggregate(), tmp_path)
    assert final.exists()
