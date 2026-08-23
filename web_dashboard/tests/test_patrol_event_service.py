import json
import urllib.error
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
    service = PatrolEventService("http://127.0.0.1:9101/api/events")

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


def test_starting_a_second_patrol_resets_the_sequence_counter():
    service = PatrolEventService("http://127.0.0.1:9101/api/events")

    with patch(
        "web_dashboard.services.patrol_event_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        service.start_patrol()
        service.end_patrol()
        service.start_patrol()

    last_start_body = json.loads(urlopen.call_args_list[-1].args[0].data.decode("utf-8"))
    assert last_start_body["event_seq"] == 0  # fresh patrol, fresh sequence
