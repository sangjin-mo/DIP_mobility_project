import json
from unittest.mock import patch

import pytest

from web_dashboard.services.vision_state_service import VisionStateService


class FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload or {"accepted": True}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_only_real_state_transitions_are_sent_after_baseline():
    service = VisionStateService(
        "http://webcam-pi.local:8003/api/drive-state",
        token="secret",
    )
    states = ["STOPPED", "STOPPED", "RUNNING", "RUNNING", "STOPPED"]

    with patch(
        "web_dashboard.services.vision_state_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        results = [service.observe({"state": state, "target_speed_mps": 0.25}) for state in states]

    assert urlopen.call_count == 2
    assert [result["changed"] for result in results] == [False, False, True, False, True]
    first_transition = urlopen.call_args_list[0].args[0]
    payload = json.loads(first_transition.data)
    assert payload["state"] == "RUNNING"
    assert payload["previous_state"] == "STOPPED"
    assert first_transition.headers["Authorization"] == "Bearer secret"


def test_unconfigured_sender_still_deduplicates_repeated_state():
    service = VisionStateService(None)

    baseline = service.observe({"state": "STOPPED"})
    repeated = service.observe({"state": "STOPPED"})
    transition = service.observe({"state": "RUNNING"})

    assert baseline == {
        "changed": False,
        "delivered": False,
        "state": "STOPPED",
        "reason": "baseline_initialized",
    }
    assert repeated["reason"] == "unchanged"
    assert transition["reason"] == "not_configured"


def test_invalid_state_is_not_sent():
    service = VisionStateService("http://webcam-pi.local:8003/api/drive-state")
    with patch("web_dashboard.services.vision_state_service.urllib.request.urlopen") as urlopen:
        result = service.observe({"state": "UNKNOWN"})

    assert result["reason"] == "invalid_state"
    urlopen.assert_not_called()


def test_capture_now_is_forwarded_to_vision_team_api():
    service = VisionStateService(
        "http://webcam-pi.local:8003/api/drive-state",
        capture_url="http://webcam-pi.local:8002",
        token="secret",
    )
    with patch(
        "web_dashboard.services.vision_state_service.urllib.request.urlopen",
        return_value=FakeResponse({"status": "ok", "filename": "crop.jpg"}),
    ) as urlopen:
        result = service.capture_now()

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://webcam-pi.local:8002/capture-now"
    assert request.method == "POST"
    assert result["filename"] == "crop.jpg"


def test_capture_interval_is_forwarded_to_webcam_pi_endpoint():
    service = VisionStateService(None, capture_url="http://webcam-pi.local:8002")
    with patch(
        "web_dashboard.services.vision_state_service.urllib.request.urlopen",
        return_value=FakeResponse(
            {"interval_sec": 10, "requested_sec": 10, "clamped": False}
        ),
    ) as urlopen:
        service.set_capture_interval(10)

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://webcam-pi.local:8002/set-interval"
    assert json.loads(request.data) == {"interval_sec": 10}


@pytest.mark.parametrize("interval", [0.1, 10.1])
def test_capture_interval_rejects_values_outside_dashboard_range(interval):
    service = VisionStateService(None, capture_url="http://webcam-pi.local:8002")
    with pytest.raises(ValueError, match="0.2초 이상 10초 이하"):
        service.set_capture_interval(interval)
