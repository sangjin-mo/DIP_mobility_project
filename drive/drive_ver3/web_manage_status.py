"""Run the existing DonkeyCar drive loop with dashboard safety diagnostics.

Use exactly like ``manage.py`` for driving::

    python web_manage_status.py drive --model models/mypilot.h5

No drive-team source file is edited.  Before ``manage.drive`` builds the
vehicle graph, this entry point substitutes compatible subclasses that expose
LiDAR and applied-throttle fields through the existing ``:9200/api/status``.
"""

from __future__ import annotations

import dashboard_control
import donkeycar as dk
import lidar_safety
import manage
from dashboard_status_integration import (
    DashboardControlPart,
    LidarSafetyGate,
    YDLidarObstaclePart,
)
from docopt import docopt


def main() -> None:
    args = docopt(manage.__doc__)
    if not args["drive"]:
        raise SystemExit("web_manage_status.py supports only the drive command")

    dashboard_control.DashboardControlPart = DashboardControlPart
    lidar_safety.LidarSafetyGate = LidarSafetyGate
    lidar_safety.YDLidarObstaclePart = YDLidarObstaclePart

    cfg = dk.load_config()
    manage.drive(
        cfg,
        model_path=args["--model"],
        use_joystick=args["--js"],
        model_type=args["--type"],
        camera_type=args["--camera"],
        meta=args["--meta"],
    )


if __name__ == "__main__":
    main()
