from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from ai_report.config import get_settings
from ai_report.models import AnalysisResult, CropState, Detection, PatrolAggregate, ZoneMetadata
from ai_report.pipeline.segment import PatrolSegmentation, ZoneWindow
from ai_report.pipeline.select_images import (
    apply_image_selection,
    copy_and_resize_images,
    select_images_for_zone,
)

PATROL_ID = "20260813_1430"


def det(state: CropState, count: int = 1, confidence: float | None = 0.9) -> Detection:
    return Detection.model_validate({"class": "tomato", "state": state.value, "count": count, "confidence": confidence})


def img(image_id: str, quality: float, detections: list[Detection], ts_ms: int = 0) -> AnalysisResult:
    return AnalysisResult(
        image_id=image_id,
        patrol_id=PATROL_ID,
        captured_at_ms=ts_ms,
        image_path=f"images/{PATROL_ID}/{image_id}.jpg",
        image_quality=quality,
        detections=detections,
    )


def window(images: list[AnalysisResult], zone_id: int = 1) -> ZoneWindow:
    return ZoneWindow(zone_id=zone_id, start_ts_ms=0, end_ts_ms=1000, telemetry=[], analysis=images, events=[])


def test_anomaly_exemplar_picked_first_highest_quality_among_disease_images():
    images = [
        img("a", 0.5, [det(CropState.SUSPECTED_DISEASE)]),
        img("b", 0.9, [det(CropState.SUSPECTED_DISEASE)]),  # higher quality disease image
        img("c", 0.99, [det(CropState.NORMAL)]),  # highest quality overall, but no disease
    ]
    selected = select_images_for_zone(window(images), undetermined_rate=0.0, settings=get_settings())
    assert selected[0] == "b"


def test_quality_floor_excludes_low_quality_images_entirely():
    settings = get_settings()
    images = [
        img("below", settings.IMAGE_QUALITY_MIN - 0.01, [det(CropState.SUSPECTED_DISEASE)]),
        img("above", settings.IMAGE_QUALITY_MIN, [det(CropState.NORMAL)]),
    ]
    selected = select_images_for_zone(window(images), undetermined_rate=0.0, settings=settings)
    assert "below" not in selected
    assert "above" in selected


def test_zone_with_no_eligible_images_returns_empty_list():
    settings = get_settings()
    images = [img("low", settings.IMAGE_QUALITY_MIN - 0.1, [det(CropState.NORMAL)])]
    assert select_images_for_zone(window(images), undetermined_rate=0.0, settings=settings) == []
    assert select_images_for_zone(None, undetermined_rate=0.0, settings=settings) == []
    assert select_images_for_zone(window([]), undetermined_rate=None, settings=settings) == []


def test_normal_representative_nearest_zone_median():
    # 정상 counts: 1, 5, 9 -> median 5. "b" (count=5) should be picked as
    # the normal representative once the anomaly slot is filled by "a".
    images = [
        img("a", 0.9, [det(CropState.SUSPECTED_DISEASE)]),
        img("low", 0.8, [det(CropState.NORMAL, count=1)]),
        img("mid", 0.8, [det(CropState.NORMAL, count=5)]),
        img("high", 0.8, [det(CropState.NORMAL, count=9)]),
    ]
    selected = select_images_for_zone(window(images), undetermined_rate=0.0, settings=get_settings())
    assert selected[0] == "a"
    assert selected[1] == "mid"


def test_undetermined_exemplar_only_selected_when_rate_exceeds_threshold():
    settings = get_settings()
    images = [
        img("normal", 0.9, [det(CropState.NORMAL)]),
        img("undet", 0.9, [det(CropState.UNDETERMINED, confidence=None)]),
    ]
    below = select_images_for_zone(window(images), undetermined_rate=settings.UNDETERMINED_FLAG_THRESHOLD, settings=settings)
    above = select_images_for_zone(window(images), undetermined_rate=settings.UNDETERMINED_FLAG_THRESHOLD + 0.01, settings=settings)
    assert "undet" not in below  # exactly at threshold: not "> threshold"
    assert "undet" in above


def test_never_selects_more_than_images_per_zone_max():
    settings = get_settings()
    images = [
        img("disease", 0.9, [det(CropState.SUSPECTED_DISEASE)]),
        img("n1", 0.9, [det(CropState.NORMAL, count=3)]),
        img("n2", 0.8, [det(CropState.NORMAL, count=3)]),
        img("undet", 0.9, [det(CropState.UNDETERMINED, confidence=None)]),
        img("extra", 0.95, [det(CropState.NORMAL, count=3)]),
    ]
    selected = select_images_for_zone(window(images), undetermined_rate=1.0, settings=settings)
    assert len(selected) <= settings.IMAGES_PER_ZONE_MAX
    assert len(selected) == len(set(selected))  # no duplicates


def _make_agg(zones: list[ZoneMetadata]) -> PatrolAggregate:
    from ai_report.models import DataCompleteness, LlmMetadata, ReportStatus

    return PatrolAggregate(
        patrol_id=PATROL_ID, patrol_date="2026-08-13", duration_min=10, overall_status=ReportStatus.NORMAL,
        llm=LlmMetadata(enabled=False),
        data_completeness=DataCompleteness(udp_received=1, udp_expected=1, rate=1.0, images_analysed=1, zone_boundary_confidence="high"),
        zones=zones,
    )


def _zone_metadata(zone_id: int, undetermined_rate: float | None = 0.0) -> ZoneMetadata:
    from ai_report.models import ZoneEnv

    return ZoneMetadata(
        zone_id=zone_id, zone_name=f"{zone_id}구역", status="정상", env=ZoneEnv(),
        observations={}, undetermined_rate=undetermined_rate, flags=[], image_ids=[], confidence="high",
    )


def test_apply_image_selection_populates_zones_without_mutating_input():
    images = [img("a", 0.9, [det(CropState.NORMAL)])]
    seg = PatrolSegmentation(patrol_id=PATROL_ID, boundary_confidence="high", windows=[window(images, zone_id=1)], patrol_start_ts_ms=0, patrol_end_ts_ms=1000)
    agg = _make_agg([_zone_metadata(1)])

    result = apply_image_selection(agg, seg, get_settings())

    assert result.zones[0].image_ids == ["a"]
    assert agg.zones[0].image_ids == []  # original untouched


def test_apply_image_selection_zone_with_no_matching_window_gets_empty_list():
    seg = PatrolSegmentation(patrol_id=PATROL_ID, boundary_confidence="high", windows=[], patrol_start_ts_ms=0, patrol_end_ts_ms=1000)
    agg = _make_agg([_zone_metadata(99)])  # no ZoneWindow for zone 99
    result = apply_image_selection(agg, seg, get_settings())
    assert result.zones[0].image_ids == []


def test_copy_and_resize_downscales_large_image_to_long_edge(tmp_path: Path):
    settings = get_settings()
    data_root = tmp_path / "data"
    src_dir = data_root / "images" / PATROL_ID
    src_dir.mkdir(parents=True)
    # A source image well over the 768px target on its long edge.
    Image.new("RGB", (1600, 1200), (10, 20, 30)).save(src_dir / "big.jpg", "JPEG")

    analysis = img("z1_big", 0.9, [det(CropState.NORMAL)])
    analysis = analysis.model_copy(update={"image_path": f"images/{PATROL_ID}/big.jpg"})
    seg = PatrolSegmentation(
        patrol_id=PATROL_ID, boundary_confidence="high",
        windows=[window([analysis], zone_id=1)], patrol_start_ts_ms=0, patrol_end_ts_ms=1000,
    )
    agg = _make_agg([_zone_metadata(1)])
    agg = agg.model_copy(update={"zones": [agg.zones[0].model_copy(update={"image_ids": ["z1_big"]})]})

    dest_dir = tmp_path / "report"
    copied = copy_and_resize_images(agg, seg, data_root, dest_dir, settings)

    assert copied == ["z1_big"]
    out_path = dest_dir / "images" / "z1_big.jpg"
    assert out_path.exists()
    with Image.open(out_path) as resized:
        assert max(resized.size) == settings.IMAGE_RESIZE_PX
        assert resized.size[0] / resized.size[1] == pytest.approx(1600 / 1200, rel=0.01)


def test_copy_and_resize_skips_missing_source_file_without_crashing(tmp_path: Path):
    settings = get_settings()
    data_root = tmp_path / "data"
    analysis = img("ghost", 0.9, [det(CropState.NORMAL)])
    seg = PatrolSegmentation(
        patrol_id=PATROL_ID, boundary_confidence="high",
        windows=[window([analysis], zone_id=1)], patrol_start_ts_ms=0, patrol_end_ts_ms=1000,
    )
    agg = _make_agg([_zone_metadata(1)])
    agg = agg.model_copy(update={"zones": [agg.zones[0].model_copy(update={"image_ids": ["ghost"]})]})

    dest_dir = tmp_path / "report"
    copied = copy_and_resize_images(agg, seg, data_root, dest_dir, settings)  # source file never created
    assert copied == []
