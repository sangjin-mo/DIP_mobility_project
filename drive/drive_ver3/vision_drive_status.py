"""Read the drive Pi's BCM GPIO27 state on the Vision Raspberry Pi."""

from __future__ import annotations

import argparse
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Read PiRacer drive status GPIO")
    parser.add_argument("--gpio-pin", type=int, default=27)
    args = parser.parse_args()

    import RPi.GPIO as GPIO

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(args.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    print("Drive status GPIO: 0=moving, 1=stopped")
    try:
        previous = None
        while True:
            stopped = int(bool(GPIO.input(args.gpio_pin)))
            if stopped != previous:
                print(f"drive_status={stopped}")
                previous = stopped
            time.sleep(0.05)
    finally:
        GPIO.cleanup(args.gpio_pin)


if __name__ == "__main__":
    main()
