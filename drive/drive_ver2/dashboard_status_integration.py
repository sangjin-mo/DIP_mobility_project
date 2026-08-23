"""Optional dashboard diagnostics layered over the existing drive parts.

The drive team's ``dashboard_control.py``, ``lidar_safety.py``, and
``manage.py`` remain unchanged.  ``web_manage_status.py`` substitutes these
subclasses at process startup so the existing HTTP status response can also
describe the final LiDAR safety gate.
"""

from __future__ import annotations

import math
import threading
import time

try:  # Package import in tests.
    from .dashboard_control import DashboardControlPart as BaseDashboardControlPart
    from .lidar_safety import LidarSafetyGate as BaseLidarSafetyGate
    from .lidar_safety import YDLidarObstaclePart as BaseYDLidarObstaclePart
except ImportError:  # Script import on the Raspberry Pi from drive_ver2/.
    from dashboard_control import DashboardControlPart as BaseDashboardControlPart
    from lidar_safety import LidarSafetyGate as BaseLidarSafetyGate
    from lidar_safety import YDLidarObstaclePart as BaseYDLidarObstaclePart


class RoverSafetyStatus:
    """Thread-safe snapshot shared by the three existing DonkeyCar parts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.commanded_throttle = 0.0
            self.applied_throttle = 0.0
            self.drive_mode = "user"
            self.lidar_blocked = True
            self.lidar_connected = False
            self.lidar_nearest_m: float | None = None

    def update_command(self, throttle: float | None, mode: str) -> None:
        with self._lock:
            self.commanded_throttle = float(throttle or 0.0)
            self.drive_mode = mode

    def update_lidar(
        self,
        blocked: bool,
        connected: bool,
        nearest_m: float | None,
    ) -> None:
        with self._lock:
            self.lidar_blocked = bool(blocked)
            self.lidar_connected = bool(connected)
            self.lidar_nearest_m = None if nearest_m is None else float(nearest_m)

    def update_applied_throttle(self, throttle: float | None) -> None:
        with self._lock:
            self.applied_throttle = float(throttle or 0.0)

    def snapshot(self, command_state: str) -> dict:
        with self._lock:
            if command_state != "RUNNING":
                motion_state = "STOPPED"
            elif self.lidar_blocked:
                motion_state = "LIDAR_BLOCKED"
            elif self.commanded_throttle > 0 and self.applied_throttle <= 0:
                motion_state = "OUTPUT_STOPPED"
            else:
                motion_state = "RUNNING"
            return {
                "motion_state": motion_state,
                "commanded_throttle": self.commanded_throttle,
                "applied_throttle": self.applied_throttle,
                "drive_mode": self.drive_mode,
                "lidar_connected": self.lidar_connected,
                "lidar_blocked": self.lidar_blocked,
                "lidar_nearest_m": self.lidar_nearest_m,
            }


SAFETY_STATUS = RoverSafetyStatus()


def classify_lidar_scan(
    points,
    stop_distance_m: float,
    forward_center_rad: float,
    forward_half_angle_rad: float,
) -> tuple[bool, bool, float | None]:
    """Classify one successful scan without treating open space as a fault.

    A healthy 360-degree scan may contain no return inside the narrow forward
    sector when there is simply nothing in range.  The original drive part
    counted only forward points and therefore fail-safe stopped in that case.
    We fail safe only when the *whole* scan has no usable range measurement.
    """
    total_valid_points = 0
    nearest_forward = None
    for point in points:
        distance, angle = float(point.range), float(point.angle)
        if not math.isfinite(distance) or distance <= 0:
            continue
        total_valid_points += 1
        relative = math.atan2(
            math.sin(angle - forward_center_rad),
            math.cos(angle - forward_center_rad),
        )
        if abs(relative) <= forward_half_angle_rad:
            nearest_forward = (
                distance
                if nearest_forward is None
                else min(nearest_forward, distance)
            )

    scan_healthy = total_valid_points > 0
    obstacle = nearest_forward is not None and nearest_forward <= stop_distance_m
    return obstacle, scan_healthy, nearest_forward


def corrected_lidar_worker(
    port: str,
    stop_distance_m: float,
    forward_center_rad: float,
    forward_half_angle_rad: float,
    clear_scans_required: int,
    fail_safe_stop: bool,
    status_queue,
    stop_event,
) -> None:
    """YDLiDAR worker with corrected open-forward-sector handling."""
    # Import here so importing/testing the dashboard does not require the Pi SDK.
    try:
        from lidar_safety import _put_latest
    except ImportError:
        from .lidar_safety import _put_latest

    laser = None
    blocked, clear_scans = fail_safe_stop, 0
    _put_latest(status_queue, (blocked, False, None))
    try:
        import ydlidar

        ydlidar.os_init()
        laser = ydlidar.CYdLidar()
        laser.setlidaropt(ydlidar.LidarPropSerialPort, port)
        laser.setlidaropt(ydlidar.LidarPropSerialBaudrate, 115200)
        laser.setlidaropt(ydlidar.LidarPropLidarType, ydlidar.TYPE_TRIANGLE)
        laser.setlidaropt(ydlidar.LidarPropDeviceType, ydlidar.YDLIDAR_TYPE_SERIAL)
        laser.setlidaropt(ydlidar.LidarPropSampleRate, 3)
        laser.setlidaropt(ydlidar.LidarPropScanFrequency, 6.0)
        laser.setlidaropt(ydlidar.LidarPropSingleChannel, True)
        laser.setlidaropt(ydlidar.LidarPropAutoReconnect, True)
        # YDLiDAR documents the X2 usable range as 0.10 m to 8.0 m.
        laser.setlidaropt(ydlidar.LidarPropMinRange, 0.10)
        laser.setlidaropt(ydlidar.LidarPropMaxRange, 8.0)
        if not laser.initialize() or not laser.turnOn():
            raise RuntimeError("YDLiDAR X2 initialize/turnOn failed")

        scan = ydlidar.LaserScan()
        while not stop_event.is_set():
            if not laser.doProcessSimple(scan):
                blocked, clear_scans = fail_safe_stop, 0
                _put_latest(status_queue, (blocked, False, None))
                time.sleep(0.05)
                continue

            obstacle, scan_healthy, nearest = classify_lidar_scan(
                scan.points,
                stop_distance_m,
                forward_center_rad,
                forward_half_angle_rad,
            )
            if not scan_healthy:
                obstacle = fail_safe_stop

            if obstacle:
                blocked, clear_scans = True, 0
            else:
                clear_scans += 1
                if clear_scans >= clear_scans_required:
                    blocked = False
            _put_latest(status_queue, (blocked, True, nearest))
    except Exception as exc:  # noqa: BLE001 - vendor SDK raises generic errors
        print(f"LiDAR safety unavailable: {exc}")
        _put_latest(status_queue, (fail_safe_stop, False, None))
    finally:
        if laser is not None:
            try:
                laser.turnOff()
                laser.disconnecting()
            except Exception:  # noqa: BLE001, S110 - best-effort hardware cleanup
                pass


class DashboardControlPart(BaseDashboardControlPart):
    """Existing command server plus read-only final-drive diagnostics."""

    def run_threaded(self, user_angle, user_throttle, user_mode):
        angle, throttle, mode = super().run_threaded(user_angle, user_throttle, user_mode)
        SAFETY_STATUS.update_command(throttle, mode)
        return angle, throttle, mode

    def _snapshot_locked(self, command_id: str | None) -> dict:
        result = super()._snapshot_locked(command_id)
        result.update(SAFETY_STATUS.snapshot(result["state"]))
        return result


class YDLidarObstaclePart(BaseYDLidarObstaclePart):
    """Existing scanner that mirrors its latest output into the status API."""

    def run_threaded(self) -> tuple[bool, bool, float | None]:
        blocked, connected, nearest_m = super().run_threaded()
        SAFETY_STATUS.update_lidar(blocked, connected, nearest_m)
        return blocked, connected, nearest_m


class LidarSafetyGate(BaseLidarSafetyGate):
    """Existing fail-safe gate that records the throttle actually passed on."""

    def run(self, throttle, blocked: bool) -> float:
        applied = super().run(throttle, blocked)
        SAFETY_STATUS.update_applied_throttle(applied)
        return applied
