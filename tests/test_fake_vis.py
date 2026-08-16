from __future__ import annotations

from ai_report.devtools.fake_vis import generate_analysis_results, write_analysis_files
from ai_report.ingest.vis_watcher import VisWatcher

PATROL_ID = "20260813_1430"


def test_fake_vis_round_trips_through_watcher(tmp_path, store):
    results = generate_analysis_results(PATROL_ID, num_zones=3, images_per_zone=2, seed=3)
    write_analysis_files(results, tmp_path, PATROL_ID)

    watcher = VisWatcher(store, tmp_path)
    scan = watcher.scan_once(PATROL_ID)

    assert scan.new_records == len(results)
    assert scan.complete is True
    assert store.analysis_count(PATROL_ID) == len(results)


def test_fake_vis_no_complete_marker(tmp_path, store):
    results = generate_analysis_results(PATROL_ID, num_zones=1, images_per_zone=1, seed=1)
    write_analysis_files(results, tmp_path, PATROL_ID, write_complete=False)

    watcher = VisWatcher(store, tmp_path)
    scan = watcher.scan_once(PATROL_ID)
    assert scan.complete is False
