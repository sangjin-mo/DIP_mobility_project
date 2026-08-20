#!/usr/bin/env python3
"""Run the existing DonkeyCar vehicle with the separate dashboard controller.

This entry point deliberately leaves ``manage.py``, ``config.py``,
``myconfig.py`` and ``train.py`` unchanged.  It reuses ``manage.drive`` and
replaces its controller only for this process.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

try:
    from .dashboard_control import DashboardControlPart
except ImportError:  # Supports: python web_manage.py
    from dashboard_control import DashboardControlPart


class DashboardControllerAdapter:
    """Adapt dashboard commands to manage.py's four controller outputs."""

    def __init__(self, control: DashboardControlPart) -> None:
        self.control = control

    def update(self) -> None:
        self.control.update()

    def run_threaded(self, _image: Any) -> tuple[float, float, str, bool]:
        angle, throttle, mode = self.control.run_threaded(None, None, None)
        return angle, throttle, mode, False

    def shutdown(self) -> None:
        self.control.shutdown()


class WebDriveConfig:
    """Read every setting from the original config, overriding no source file."""

    USE_JOYSTICK_AS_DEFAULT = False

    def __init__(self, original: Any) -> None:
        self._original = original

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="기존 주행 코드를 수정하지 않고 웹으로 PiRacer를 구동/정지합니다."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--token", default=os.getenv("DASHBOARD_CONTROL_TOKEN"))
    parser.add_argument("--heartbeat-timeout", type=float, default=1.5)
    parser.add_argument("--max-speed", type=float, default=0.50)
    parser.add_argument("--max-throttle", type=float, default=0.20)
    parser.add_argument("--straight-steering", type=float, default=0.0)
    parser.add_argument("--model")
    parser.add_argument("--type", dest="model_type")
    parser.add_argument("--camera", choices=("single", "stereo"), default="single")
    return parser


def run_web_drive(args: argparse.Namespace) -> None:
    if not args.token:
        raise SystemExit(
            "DASHBOARD_CONTROL_TOKEN 환경변수 또는 --token 값을 지정해야 합니다."
        )

    # DonkeyCar's loader expects config.py/myconfig.py in the current car folder.
    drive_dir = Path(__file__).resolve().parent
    os.chdir(drive_dir)

    import donkeycar as dk

    try:
        from . import manage as legacy_manage
    except ImportError:  # Supports: python web_manage.py
        import manage as legacy_manage

    control = DashboardControlPart(
        host=args.host,
        port=args.port,
        token=args.token,
        heartbeat_timeout_s=args.heartbeat_timeout,
        max_speed_mps=args.max_speed,
        max_throttle=args.max_throttle,
        straight_steering=args.straight_steering,
    )
    adapter = DashboardControllerAdapter(control)

    # manage.py looks up this symbol when drive() runs. Replacing the symbol in
    # memory keeps the team's source file untouched and affects this process only.
    original_controller = legacy_manage.LocalWebController
    legacy_manage.LocalWebController = lambda: adapter
    try:
        cfg = WebDriveConfig(dk.load_config())
        legacy_manage.drive(
            cfg,
            model_path=args.model,
            use_joystick=False,
            model_type=args.model_type,
            camera_type=args.camera,
            meta=[],
        )
    finally:
        legacy_manage.LocalWebController = original_controller
        control.shutdown()


def main() -> None:
    run_web_drive(build_parser().parse_args())


if __name__ == "__main__":
    main()
