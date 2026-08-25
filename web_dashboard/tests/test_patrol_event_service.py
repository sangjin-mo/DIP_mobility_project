import json
import sys
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from web_dashboard.services.patrol_event_service import PatrolEventService


class FakeResponse:
    def __init__(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return b'{"status": "accepted", "duplicate": false}'


def test_unconfigured_service_does_nothing():
    service = PatrolEventService(None)

    assert service.configured is False
    assert service.start_patrol() is None
    assert service.end_patrol() is None
    assert service.active_patrol_id is None


def test_start_patrol_posts_patrol_start_and_tracks_active_id():
    service = PatrolEventService("http://127.0.0.1:9101/api/events")

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        patrol_id = service.start_patrol()

    assert patrol_id is not None
    assert service.active_patrol_id == patrol_id

    request = urlopen.call_args.args[0]
    body = json.loads(request.data.decode("utf-8"))
    assert body["patrol_id"] == patrol_id
    assert body["type"] == "PATROL_START"
    assert body["event_seq"] == 0
    assert request.headers["Content-type"] == "application/json"


def test_end_patrol_posts_patrol_end_for_the_active_patrol_and_clears_it():
    service = PatrolEventService("http://127.0.0.1:9101/api/events", auto_classify_enabled=False)

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        started_id = service.start_patrol()
        ended_id = service.end_patrol()

    assert ended_id == started_id
    assert service.active_patrol_id is None

    end_call = urlopen.call_args_list[-1]
    body = json.loads(end_call.args[0].data.decode("utf-8"))
    assert body["type"] == "PATROL_END"
    assert body["patrol_id"] == started_id
    assert body["event_seq"] == 1  # second event for this patrol


def test_end_patrol_without_a_prior_start_is_a_noop():
    service = PatrolEventService("http://127.0.0.1:9101/api/events")

    with patch("web_dashboard.services.patrol_event_service.urllib.request.urlopen") as urlopen:
        result = service.end_patrol()

    assert result is None
    urlopen.assert_not_called()


def test_network_failure_is_swallowed_not_raised():
    """Best-effort: notifying ai_report must never fail the caller (the
    real drive command has already succeeded by the time this runs)."""
    service = PatrolEventService("http://127.0.0.1:9101/api/events")

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        patrol_id = service.start_patrol()  # must not raise

    assert patrol_id is not None  # patrol_id is still generated/tracked locally
    assert service.active_patrol_id == patrol_id


def test_starting_a_second_patrol_continues_the_sequence_counter():
    """`event_seq` is monotonic per process, deliberately not per patrol.

    It used to reset to 0 on every START. Two patrols inside one minute share
    a patrol_id (C3 fixes it at YYYYMMDD_HHMM), so the second patrol re-posted
    event_seq=0 and `Store.insert_event` dedupped it on (patrol_id,
    event_seq). Its PATROL_END was dropped as a duplicate, so
    `event_api.py` never fired `on_patrol_end` and that patrol got no report.
    """
    service = PatrolEventService("http://127.0.0.1:9101/api/events", auto_classify_enabled=False)

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        service.start_patrol()
        service.end_patrol()
        service.start_patrol()

    last_start_body = json.loads(urlopen.call_args_list[-1].args[0].data.decode("utf-8"))
    assert last_start_body["event_seq"] == 2


def test_end_patrol_triggers_classify_subprocess_for_todays_received_dir(tmp_path: Path):
    received_root = tmp_path / "received"
    today_dir = received_root / datetime.now().strftime("%Y-%m-%d")
    today_dir.mkdir(parents=True)
    log_dir = tmp_path / "logs"
    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        auto_classify_enabled=True,
        received_root=received_root,
        log_dir=log_dir,
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ), patch("web_dashboard.services.patrol_event_service.subprocess.Popen") as popen:
        patrol_id = service.start_patrol()
        service.end_patrol()
        # Transfer-then-classify runs on its own thread so STOP returns
        # immediately -- see PatrolEventService._trigger_classification.
        for thread in _auto_classify_threads():
            thread.join(timeout=5)

    popen.assert_called_once()
    argv = popen.call_args.args[0]
    assert argv[:3] == [sys.executable, "-m", "vision.image_analysis.system.classify"]
    assert "--patrol-id" in argv and patrol_id in argv
    assert "--source-dir" in argv and str(today_dir) in argv
    assert log_dir.is_dir()


def test_end_patrol_skips_classify_when_disabled(tmp_path: Path):
    received_root = tmp_path / "received"
    (received_root / datetime.now().strftime("%Y-%m-%d")).mkdir(parents=True)
    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        auto_classify_enabled=False,
        received_root=received_root,
        log_dir=tmp_path / "logs",
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ), patch("web_dashboard.services.patrol_event_service.subprocess.Popen") as popen:
        service.start_patrol()
        service.end_patrol()

    popen.assert_not_called()


def test_end_patrol_skips_classify_when_source_dir_missing(tmp_path: Path):
    """No images arrived today (or pc_server never ran) -- must not spawn
    classify.py against a directory that doesn't exist."""
    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        auto_classify_enabled=True,
        received_root=tmp_path / "received",  # deliberately never created
        log_dir=tmp_path / "logs",
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ), patch("web_dashboard.services.patrol_event_service.subprocess.Popen") as popen:
        service.start_patrol()
        service.end_patrol()

    popen.assert_not_called()


def test_end_patrol_skips_classify_when_patrol_end_post_fails(tmp_path: Path):
    """ai_report never saw PATROL_END, so it will never poll for this
    patrol's analysis files -- running classify.py would be pure waste
    (and an unwanted OpenAI call)."""
    received_root = tmp_path / "received"
    (received_root / datetime.now().strftime("%Y-%m-%d")).mkdir(parents=True)
    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        auto_classify_enabled=True,
        received_root=received_root,
        log_dir=tmp_path / "logs",
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ):
        service.start_patrol()

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ), patch("web_dashboard.services.patrol_event_service.subprocess.Popen") as popen:
        service.end_patrol()

    popen.assert_not_called()


def test_patrol_id_uses_local_time_not_utc():
    """`patrol_id` is also the report's date and directory name.

    `ai_report/pipeline/aggregate.py::_patrol_date` slices the report date
    straight out of its YYYYMMDD prefix, and `_spawn_classify` reads
    `received/{local date}/`. Minting it in UTC put a KST operator's 17:32
    patrol in `20260824_0832` and dated anything before 09:00 to the previous
    day.
    """
    service = PatrolEventService("http://127.0.0.1:9101/api/events")

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ):
        patrol_id = service.start_patrol()

    assert patrol_id == datetime.now().strftime("%Y%m%d_%H%M")


def test_event_seq_does_not_restart_between_patrols(tmp_path: Path):
    """Two patrols inside one minute share a patrol_id, so restarting
    `event_seq` at 0 made the second patrol's events collide with the first's
    on `Store.insert_event`'s (patrol_id, event_seq) key.

    They were dropped as duplicates, `event_api.py` never fired
    `on_patrol_end`, and that patrol silently got no report at all.
    """
    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        auto_classify_enabled=False,
        received_root=tmp_path / "received",
        log_dir=tmp_path / "logs",
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        service.start_patrol()
        service.end_patrol()
        service.start_patrol()
        service.end_patrol()

    seqs = [
        json.loads(call.args[0].data.decode("utf-8"))["event_seq"]
        for call in urlopen.call_args_list
    ]
    assert seqs == [0, 1, 2, 3], "event_seq must be monotonic across patrols, not reset per patrol"


def test_images_are_transferred_before_classification_is_spawned(tmp_path: Path):
    """The bug that emptied every report: images sit on the webcam Pi until
    something calls its `/trigger-upload`, and nothing in the STOP path did.
    classify.py therefore ran against a directory holding none of this
    patrol's frames and wrote `_COMPLETE` on zero images.
    """
    received = tmp_path / "received" / datetime.now().strftime("%Y-%m-%d")
    received.mkdir(parents=True)
    order: list[str] = []

    def fake_transfer() -> dict:
        order.append("transfer")
        return {"requested": 3, "success": 3, "failed": 0}

    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        received_root=tmp_path / "received",
        log_dir=tmp_path / "logs",
        transfer_images=fake_transfer,
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ), patch.object(service, "_spawn_classify", side_effect=lambda *a: order.append("classify")):
        service.start_patrol()
        service.end_patrol()
        for thread in _auto_classify_threads():
            thread.join(timeout=5)

    assert order == ["transfer", "classify"]


def test_transfer_failure_still_classifies_what_already_arrived(tmp_path: Path):
    """An unreachable Pi must degrade to "classify what we have", not skip
    classification altogether."""
    (tmp_path / "received" / datetime.now().strftime("%Y-%m-%d")).mkdir(parents=True)
    order: list[str] = []

    def failing_transfer() -> dict:
        raise ConnectionError("pi unreachable")

    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        received_root=tmp_path / "received",
        log_dir=tmp_path / "logs",
        transfer_images=failing_transfer,
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ), patch.object(service, "_spawn_classify", side_effect=lambda *a: order.append("classify")):
        service.start_patrol()
        service.end_patrol()
        for thread in _auto_classify_threads():
            thread.join(timeout=5)

    assert order == ["classify"]


def test_classify_is_spawned_without_a_drive_window_filter(tmp_path: Path):
    """Scoping classification to the drive window was the wrong question:
    `INTEGRATION_RUNBOOK.md` states camera capture is independent of vehicle
    control, so the two windows routinely do not overlap. classify.py's
    ledger decides what is new instead.
    """
    received_root = tmp_path / "received"
    (received_root / datetime.now().strftime("%Y-%m-%d")).mkdir(parents=True)

    service = PatrolEventService(
        "http://127.0.0.1:9101/api/events",
        received_root=received_root,
        log_dir=tmp_path / "logs",
    )

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ), patch("web_dashboard.services.patrol_event_service.subprocess.Popen") as popen:
        service.start_patrol()
        service.end_patrol()
        for thread in _auto_classify_threads():
            thread.join(timeout=5)

    argv = popen.call_args.args[0]
    assert "--after-ts-ms" not in argv
    assert "--before-ts-ms" not in argv
    assert argv[:3] == [sys.executable, "-m", "vision.image_analysis.system.classify"]


def _auto_classify_threads():
    import threading

    return [t for t in threading.enumerate() if t.name.startswith("auto-classify-")]
