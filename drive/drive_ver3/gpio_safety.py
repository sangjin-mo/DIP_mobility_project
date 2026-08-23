"""GPIO stop input and drive-state output for the PiRacer drive Pi."""

from __future__ import annotations

import time
import threading
import uuid


class VisionGpioStopPart:
    """Latch a dashboard STOP when the Vision Pi raises the input pin."""

    def __init__(self, control_part, input_pin: int, poll_interval_s: float = 0.01) -> None:
        self.control_part = control_part
        self.input_pin = input_pin
        self.poll_interval_s = poll_interval_s
        self._gpio = None
        self._previous_level = False
        self._stop_event = threading.Event()

    def update(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.input_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        # Treat a pin already held HIGH at startup as a valid stop event.
        self._previous_level = False
        try:
            while not self._stop_event.is_set():
                level = bool(GPIO.input(self.input_pin))
                if level and not self._previous_level:
                    self.control_part.apply_command(
                        {
                            "command": "STOP",
                            "command_id": f"vision-gpio-{uuid.uuid4()}",
                            "sent_at_ms": int(time.time() * 1000),
                            "source": "VISION_GPIO",
                            "reason": "STOP_SIGN",
                        }
                    )
                    print(f"Vision GPIO STOP: BCM GPIO{self.input_pin}=1")
                self._previous_level = level
                self._stop_event.wait(self.poll_interval_s)
        finally:
            GPIO.cleanup(self.input_pin)

    def shutdown(self) -> None:
        self._stop_event.set()


class DriveStatusGpioPart:
    """Publish final drive state: 0 while moving, 1 while stopped."""

    def __init__(self, output_pin: int) -> None:
        self.output_pin = output_pin
        self._gpio = None

    def update(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.output_pin, GPIO.OUT, initial=GPIO.HIGH)

    def run(self, throttle) -> None:
        if self._gpio is None:
            return
        self._gpio.output(self.output_pin, self._gpio.LOW if float(throttle or 0.0) > 0 else self._gpio.HIGH)

    def shutdown(self) -> None:
        if self._gpio is not None:
            self._gpio.output(self.output_pin, self._gpio.HIGH)
            self._gpio.cleanup(self.output_pin)