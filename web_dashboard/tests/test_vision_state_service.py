import json
from unittest.mock import patch

from web_dashboard.services.vision_state_service import VisionStateService


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"accepted": true}'


def test_only_real_state_transitions_are_sent_after_baseline():
    service = VisionStateService(
        "http://webcam-pi.local:8002/api/drive-state",
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
    service = VisionStateService("http://webcam-pi.local:8002/api/drive-state")
    with patch("web_dashboard.services.vision_state_service.urllib.request.urlopen") as urlopen:
        result = service.observe({"state": "UNKNOWN"})

    assert result["reason"] == "invalid_state"
    urlopen.assert_not_called()


def test_capture_mode_is_forwarded_to_webcam_pi_endpoint():
    service = VisionStateService(
        "http://webcam-pi.local:8002/api/drive-state",
        token="secret",
    )
    with patch(
        "web_dashboard.services.vision_state_service.urllib.request.urlopen",
        return_value=FakeResponse(),
    ) as urlopen:
        service.set_capture_mode(True)

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://webcam-pi.local:8002/api/capture-mode"
    assert request.method == "POST"
    assert json.loads(request.data) == {"enabled": True}
