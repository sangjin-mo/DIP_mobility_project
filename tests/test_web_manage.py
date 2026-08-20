import argparse

from drive.dashboard_control import DashboardControlPart
from drive.web_manage import DashboardControllerAdapter, WebDriveConfig


def make_control() -> DashboardControlPart:
    return DashboardControlPart(
        host="127.0.0.1",
        port=0,
        token="secret",
        heartbeat_timeout_s=1.5,
        max_speed_mps=0.5,
        max_throttle=0.2,
    )


def test_adapter_returns_manage_controller_shape() -> None:
    control = make_control()
    adapter = DashboardControllerAdapter(control)
    control.apply_command(
        {
            "command_id": "start-1",
            "command": "START",
            "target_speed_mps": 0.25,
        }
    )

    assert adapter.run_threaded(None) == (0.0, 0.1, "user", False)


def test_adapter_stop_forces_zero_throttle() -> None:
    control = make_control()
    adapter = DashboardControllerAdapter(control)
    control.apply_command(
        {
            "command_id": "start-1",
            "command": "START",
            "target_speed_mps": 0.25,
        }
    )
    control.apply_command({"command_id": "stop-1", "command": "STOP"})

    assert adapter.run_threaded(None) == (0.0, 0.0, "user", False)


def test_config_proxy_forces_web_controller_without_editing_original() -> None:
    original = argparse.Namespace(USE_JOYSTICK_AS_DEFAULT=True, DRIVE_LOOP_HZ=20)
    config = WebDriveConfig(original)

    assert config.USE_JOYSTICK_AS_DEFAULT is False
    assert config.DRIVE_LOOP_HZ == 20
    assert original.USE_JOYSTICK_AS_DEFAULT is True
