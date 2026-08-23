"""Vision-Pi stop-sign detector that sends one authenticated stop request.

Install on the Vision Pi: ``pip install ultralytics opencv-python``.
The default COCO model contains the ``stop sign`` class.  Use a local model
path with ``--model`` when the Vision Pi has no network access.
"""

from __future__ import annotations

import argparse
import os
import time

from vision_gpio import VisionStopGpioSender
from vision_stop_client import send_stop_sign_stop


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop PiRacer after detecting a stop sign")
    parser.add_argument("--control-url", default=os.getenv("PIRACER_CONTROL_URL"), required=os.getenv("PIRACER_CONTROL_URL") is None)
    parser.add_argument("--token", default=os.getenv("PIRACER_CONTROL_TOKEN"), required=os.getenv("PIRACER_CONTROL_TOKEN") is None)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--confidence", type=float, default=0.65)
    parser.add_argument("--confirm-frames", type=int, default=3)
    parser.add_argument("--cooldown-s", type=float, default=5.0)
    parser.add_argument("--gpio-pin", type=int, default=17)
    parser.add_argument("--no-gpio", action="store_true")
    parser.add_argument("--gpio-pulse-s", type=float, default=0.2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.confirm_frames < 1 or not 0 < args.confidence <= 1:
        raise ValueError("confidence must be (0, 1] and confirm-frames must be positive")
    import cv2
    from ultralytics import YOLO

    model = YOLO(args.model)
    camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        raise RuntimeError(f"cannot open vision camera {args.camera}")
    gpio_sender = None if args.no_gpio else VisionStopGpioSender(args.gpio_pin, args.gpio_pulse_s)
    if gpio_sender is not None:
        gpio_sender.start()
    consecutive, last_sent_at = 0, 0.0
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                time.sleep(0.05)
                continue
            result = model(frame, verbose=False)[0]
            names = result.names
            detected = any(
                names[int(box.cls[0])] == "stop sign" and float(box.conf[0]) >= args.confidence
                for box in result.boxes
            )
            consecutive = consecutive + 1 if detected else 0
            if consecutive >= args.confirm_frames and time.monotonic() - last_sent_at >= args.cooldown_s:
                last_sent_at = time.monotonic()
                consecutive = 0
                if gpio_sender is not None:
                    gpio_sender.send_stop()
                try:
                    response = send_stop_sign_stop(args.control_url, args.token)
                    print(f"stop sign confirmed; drive Pi response: {response}")
                except Exception as exc:
                    # Keep monitoring and retry after the cooldown if Wi-Fi or
                    # the drive Pi API is temporarily unavailable.
                    print(f"stop sign confirmed but STOP request failed: {exc}")
    finally:
        camera.release()
        if gpio_sender is not None:
            gpio_sender.close()


if __name__ == "__main__":
    main()
