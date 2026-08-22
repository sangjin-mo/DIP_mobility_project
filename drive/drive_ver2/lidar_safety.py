"""YDLiDAR X2 obstacle safety parts for the DonkeyCar vehicle loop.

The reader runs separately from the vehicle loop.  Until it has received
valid scans, it reports ``blocked=True`` by default so a disconnected LiDAR
cannot accidentally allow the vehicle to drive.
"""

from __future__ import annotations

import math
import multiprocessing as mp
import queue
import threading
import time


def _put_latest(status_queue, status: tuple[bool, bool, float | None]) -> None:
    """Keep one current LiDAR state without allowing a scan backlog."""
    try:
        status_queue.put_nowait(status)
        return
    except queue.Full:
        pass
    try:
        status_queue.get_nowait()
    except queue.Empty:
        pass
    try:
        status_queue.put_nowait(status)
    except queue.Full:
        pass


def _lidar_worker(
    port: str,
    stop_distance_m: float,
    forward_center_rad: float,
    forward_half_angle_rad: float,
    clear_scans_required: int,
    fail_safe_stop: bool,
    status_queue,
    stop_event,
) -> None:
    """Run the vendor SDK outside the model/camera Python process."""
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
        laser.setlidaropt(ydlidar.LidarPropMinRange, 0.02)
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

            nearest, valid_points = None, 0
            for point in scan.points:
                distance, angle = float(point.range), float(point.angle)
                if not math.isfinite(distance) or distance <= 0:
                    continue
                relative = math.atan2(
                    math.sin(angle - forward_center_rad),
                    math.cos(angle - forward_center_rad),
                )
                if abs(relative) <= forward_half_angle_rad:
                    valid_points += 1
                    nearest = distance if nearest is None else min(nearest, distance)

            obstacle = nearest is not None and nearest <= stop_distance_m
            if valid_points == 0 and fail_safe_stop:
                obstacle = True
            if obstacle:
                blocked, clear_scans = True, 0
            else:
                clear_scans += 1
                if clear_scans >= clear_scans_required:
                    blocked = False
            _put_latest(status_queue, (blocked, True, nearest))
    except Exception as exc:
        print(f"LiDAR safety unavailable: {exc}")
        _put_latest(status_queue, (fail_safe_stop, False, None))
    finally:
        if laser is not None:
            try:
                laser.turnOff()
                laser.disconnecting()
            except Exception:
                pass


class YDLidarObstaclePart:
    """Report an obstacle in the configured YDLiDAR X2 scan sector."""

    def __init__(
        self,
        port: str,
        stop_distance_m: float = 0.10,
        forward_center_deg: float = 0.0,
        forward_half_angle_deg: float = 20.0,
        clear_scans_required: int = 3,
        fail_safe_stop: bool = True,
    ) -> None:
        if stop_distance_m <= 0 or forward_half_angle_deg <= 0 or clear_scans_required < 1:
            raise ValueError("invalid LiDAR safety configuration")
        self.port = port
        self.stop_distance_m = stop_distance_m
        self.forward_center_rad = math.radians(forward_center_deg)
        self.forward_half_angle_rad = math.radians(forward_half_angle_deg)
        self.clear_scans_required = clear_scans_required
        self.fail_safe_stop = fail_safe_stop
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._blocked = fail_safe_stop
        self._connected = False
        self._nearest_m: float | None = None
        self._worker = None
        self._worker_stop = None
        self._status_queue = None

    def update(self) -> None:
        """Start isolated LiDAR process and relay only its safety state."""
        try:
            context = mp.get_context("spawn")
            self._status_queue = context.Queue(maxsize=1)
            self._worker_stop = context.Event()
            self._worker = context.Process(
                target=_lidar_worker,
                args=(
                    self.port,
                    self.stop_distance_m,
                    self.forward_center_rad,
                    self.forward_half_angle_rad,
                    self.clear_scans_required,
                    self.fail_safe_stop,
                    self._status_queue,
                    self._worker_stop,
                ),
                daemon=True,
                name="ydlidar-safety",
            )
            self._worker.start()
            while not self._stop_event.is_set():
                self._drain_status()
                time.sleep(0.01)
        finally:
            self._stop_worker()

    def run_threaded(self) -> tuple[bool, bool, float | None]:
        """Return blocked, connected, and nearest forward range in metres."""
        with self._lock:
            return self._blocked, self._connected, self._nearest_m

    def shutdown(self) -> None:
        self._stop_event.set()
        self._stop_worker()

    def _drain_status(self) -> None:
        if self._status_queue is None:
            return
        latest = None
        while True:
            try:
                latest = self._status_queue.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        blocked, connected, nearest = latest
        with self._lock:
            was_blocked = self._blocked
            was_connected = self._connected
            self._blocked, self._connected, self._nearest_m = blocked, connected, nearest
        if connected and not was_connected:
            print(
                "LiDAR safety connected: "
                f"stop <= {self.stop_distance_m:.2f} m, "
                f"sector +/- {math.degrees(self.forward_half_angle_rad):.0f} deg"
            )
        if blocked != was_blocked:
            if blocked:
                distance_text = "no valid scan point" if nearest is None else f"{nearest:.3f} m"
                print(f"LiDAR safety STOP: nearest={distance_text}")
            else:
                print("LiDAR safety CLEAR: dashboard drive may resume")

    def _stop_worker(self) -> None:
        if self._worker_stop is not None:
            self._worker_stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            if self._worker.is_alive():
                self._worker.terminate()
                self._worker.join(timeout=1.0)
        self._worker = None


class LidarSafetyGate:
    """Final actuator gate; LiDAR has no connection to steering data."""

    def run(self, throttle, blocked: bool) -> float:
        if blocked:
            return 0.0
        return float(throttle or 0.0)
