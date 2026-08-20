import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from web_dashboard.services.control_service import (
    ControlCommandError,
    ControlUnavailableError,
    DriveCommand,
    RoverControlService,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def test_unconfigured_control_is_unavailable():
    service = RoverControlService(None)

    with pytest.raises(ControlUnavailableError):
        service.send(DriveCommand.STOP)


def test_start_forwards_speed_and_token():
    service = RoverControlService(
        "http://rover.local:9200/api/control",
        token="secret",
    )
    with patch(
        "web_dashboard.services.control_service.urllib.request.urlopen",
        return_value=FakeResponse({"accepted": True, "state": "RUNNING"}),
    ) as urlopen:
        result = service.send(DriveCommand.START, 0.25)

    request = urlopen.call_args.args[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.headers["Authorization"] == "Bearer secret"
    assert body["command"] == "START"
    assert body["target_speed_mps"] == 0.25
    assert result["accepted"] is True
    assert result["rover"]["state"] == "RUNNING"


def test_rejected_command_is_not_reported_as_success():
    service = RoverControlService("http://rover.local:9200/api/control")
    with patch(
        "web_dashboard.services.control_service.urllib.request.urlopen",
        return_value=FakeResponse({"accepted": False, "reason": "battery low"}),
    ), pytest.raises(ControlCommandError, match="battery low"):
        service.send(DriveCommand.STOP)


def test_network_failure_is_unavailable():
    service = RoverControlService("http://rover.local:9200/api/control")
    error = urllib.error.URLError("offline")
    with patch(
        "web_dashboard.services.control_service.urllib.request.urlopen",
        MagicMock(side_effect=error),
    ), pytest.raises(ControlUnavailableError):
        service.send(DriveCommand.STOP)


def test_heartbeat_is_forwarded_without_speed():
    service = RoverControlService("http://rover.local:9200/api/control")
    with patch(
        "web_dashboard.services.control_service.urllib.request.urlopen",
        return_value=FakeResponse({"accepted": True, "state": "RUNNING"}),
    ) as urlopen:
        service.send(DriveCommand.HEARTBEAT)

    body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
    assert body["command"] == "HEARTBEAT"
    assert "target_speed_mps" not in body
