import threading
import time
import urllib.request

import pytest

from drive.dashboard_control import CommandRejected, DashboardControlPart


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_control(
    clock: FakeClock | None = None, use_pilot_steering: bool = False
) -> DashboardControlPart:
    return DashboardControlPart(
        host="127.0.0.1",
        port=0,
        token="secret",
        heartbeat_timeout_s=1.5,
        max_speed_mps=0.5,
        max_throttle=0.2,
        straight_steering=0.0,
        use_pilot_steering=use_pilot_steering,
        clock=clock or FakeClock(),
    )


def command(name: str, **extra) -> dict:
    return {"command_id": "cmd-1", "command": name, "sent_at_ms": 1, **extra}


def test_stopped_state_forces_zero_actuator_output():
    control = make_control()

    assert control.run_threaded(0.8, 0.9, "local") == (0.0, 0.0, "user")


def test_start_maps_target_speed_to_bounded_throttle():
    control = make_control()

    result = control.apply_command(command("START", target_speed_mps=0.25))

    assert result["state"] == "RUNNING"
    assert control.run_threaded(None, None, None) == (0.0, 0.1, "user")


def test_running_defaults_to_user_mode_straight_driving():
    control = make_control()
    control.apply_command(command("START", target_speed_mps=0.25))

    assert control.run_threaded(None, None, None) == (0.0, 0.1, "user")


def test_pilot_steering_uses_local_angle_mode_when_running():
    control = make_control(use_pilot_steering=True)
    control.apply_command(command("START", target_speed_mps=0.25))

    assert control.run_threaded(None, None, None) == (0.0, 0.1, "local_angle")


def test_pilot_steering_still_forces_user_mode_when_stopped():
    control = make_control(use_pilot_steering=True)

    assert control.run_threaded(0.8, 0.9, "local") == (0.0, 0.0, "user")


def test_missing_heartbeat_stops_locally():
    clock = FakeClock()
    control = make_control(clock)
    control.apply_command(command("START", target_speed_mps=0.25))

    clock.now += 1.6

    assert control.run_threaded(None, None, None) == (0.0, 0.0, "user")
    assert control.snapshot()["state"] == "STOPPED"


def test_heartbeat_keeps_running():
    clock = FakeClock()
    control = make_control(clock)
    control.apply_command(command("START", target_speed_mps=0.25))
    clock.now += 1.0
    control.apply_command(command("HEARTBEAT"))
    clock.now += 1.0

    assert control.snapshot()["state"] == "RUNNING"


def test_stop_allows_a_later_restart():
    control = make_control()
    control.apply_command(command("START", target_speed_mps=0.1))
    control.apply_command(command("STOP"))

    assert control.run_threaded(None, None, None) == (0.0, 0.0, "user")
    assert control.apply_command(command("START", target_speed_mps=0.1))["state"] == "RUNNING"


def test_speed_above_calibrated_limit_is_rejected():
    control = make_control()

    with pytest.raises(CommandRejected, match="at most 0.500"):
        control.apply_command(command("START", target_speed_mps=0.6))


def test_shared_token_uses_bearer_authentication():
    control = make_control()

    assert control.authenticate("Bearer secret") is True
    assert control.authenticate("Bearer wrong") is False


def test_pi_server_serves_two_button_web_interface():
    control = make_control()
    thread = threading.Thread(target=control.update, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            if control._server is not None:
                break
            time.sleep(0.01)
        assert control._server is not None
        port = control._server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
            html = response.read().decode("utf-8")

        assert 'id="start"' in html
        assert 'id="stop"' in html
        assert "emergency-stop" not in html
    finally:
        control.shutdown()
        thread.join(timeout=1)
