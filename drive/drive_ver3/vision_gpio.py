"""GPIO helpers intended to run on the separate Vision Raspberry Pi."""

from __future__ import annotations

import time


class VisionStopGpioSender:
    """Send an active-high stop pulse from Vision Pi BCM GPIO17."""

    def __init__(self, pin: int = 17, pulse_s: float = 0.2) -> None:
        if pulse_s <= 0:
            raise ValueError("pulse_s must be positive")
        self.pin = pin
        self.pulse_s = pulse_s
        self._gpio = None

    def start(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT, initial=GPIO.LOW)

    def send_stop(self) -> None:
        if self._gpio is None:
            raise RuntimeError("call start() before send_stop()")
        self._gpio.output(self.pin, self._gpio.HIGH)
        time.sleep(self.pulse_s)
        self._gpio.output(self.pin, self._gpio.LOW)

    def close(self) -> None:
        if self._gpio is not None:
            self._gpio.output(self.pin, self._gpio.LOW)
            self._gpio.cleanup(self.pin)
