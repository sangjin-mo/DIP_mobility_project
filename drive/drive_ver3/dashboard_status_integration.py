"""Optional dashboard diagnostics layered over the existing drive parts.

The drive team's ``dashboard_control.py``, ``lidar_safety.py``, and
``manage.py`` remain unchanged.  ``web_manage_status.py`` substitutes these
subclasses at process startup so the existing HTTP status response can also
describe the final LiDAR safety gate.
"""

from __future__ import annotations

import threading

try:  # Package import in tests.
    from .dashboard_control import DashboardControlPart as BaseDashboardControlPart
    from .lidar_safety import LidarSafetyGate as BaseLidarSafetyGate
    from .lidar_safety import YDLidarObstaclePart as BaseYDLidarObstaclePart
except ImportError:  # Script import on the Raspberry Pi from drive_ver3/.
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
