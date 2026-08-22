from drive.drive_ver3.dashboard_status_integration import (
    SAFETY_STATUS,
    DashboardControlPart,
    LidarSafetyGate,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_control() -> DashboardControlPart:
    SAFETY_STATUS.reset()
    return DashboardControlPart(
        host="127.0.0.1",
        port=0,
        token="secret",
        heartbeat_timeout_s=1.5,
        max_speed_mps=0.5,
        max_throttle=0.45,
        straight_steering=0.0,
        use_pilot_steering=True,
        clock=FakeClock(),
    )


def test_status_distinguishes_running_command_from_lidar_blocked_output():
    control = make_control()
    control.apply_command(
        {"command_id": "start-1", "command": "START", "target_speed_mps": 0.35}
    )
    _angle, throttle, _mode = control.run_threaded(None, None, None)
    SAFETY_STATUS.update_lidar(True, True, 0.12)
    assert LidarSafetyGate().run(throttle, True) == 0.0

    status = control.snapshot()

    assert status["state"] == "RUNNING"
    assert status["target_speed_mps"] == 0.35
    assert status["commanded_throttle"] == 0.315
    assert status["applied_throttle"] == 0.0
    assert status["motion_state"] == "LIDAR_BLOCKED"
    assert status["lidar_connected"] is True
    assert status["lidar_blocked"] is True
    assert status["lidar_nearest_m"] == 0.12


def test_clear_lidar_reports_applied_drive_output():
    control = make_control()
    control.apply_command(
        {"command_id": "start-1", "command": "START", "target_speed_mps": 0.25}
    )
    _angle, throttle, _mode = control.run_threaded(None, None, None)
    SAFETY_STATUS.update_lidar(False, True, 0.8)
    assert LidarSafetyGate().run(throttle, False) == 0.225

    status = control.snapshot()

    assert status["motion_state"] == "RUNNING"
    assert status["applied_throttle"] == 0.225
    assert status["lidar_blocked"] is False
