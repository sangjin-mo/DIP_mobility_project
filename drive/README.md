# PiRacer drive code

This directory is a DonkeyCar `mycar`-style project uploaded to `main` in
commits `5b1d3a6` and `40aa77e`.

## What the uploaded files do

- `manage.py`: assembles the camera, web/joystick controller, optional neural
  pilot, drive-mode selector, PCA9685 steering/throttle actuators, and tub
  recorder into the DonkeyCar vehicle loop.
- `config.py`: generated/default DonkeyCar configuration. It currently selects
  `SERVO_ESC`, steering I2C address `0x40`, and throttle I2C address `0x60`.
- `myconfig.py`: local overrides. Hardware calibration belongs here rather than
  in `config.py`.
- `train.py`: legacy TensorFlow/Keras training pipeline for DonkeyCar tub data.
  It is not needed for dashboard start/stop testing.

The uploaded code is a generic DonkeyCar scaffold. It does not contain this
project's UDP telemetry sender, patrol events, zone-marker recognition, or a
trained line-following model.

## Dashboard integration added in this repository

`dashboard_control.py` adds a small authenticated control API to the same
process that owns the DonkeyCar actuators:

- `START`: maps the requested target speed linearly onto a calibrated maximum
  throttle and drives with the configured straight steering value.
- `STOP`: the single user-facing safety stop; immediately outputs neutral
  steering and zero throttle.
- `HEARTBEAT`: keeps a running command alive. Missing heartbeats force a local
  stop even if the PC, browser, or Wi-Fi fails.

By default this drives straight only. Set `DASHBOARD_USE_PILOT_STEERING = True`
in `myconfig.py` to hand steering to a trained pilot model instead (DonkeyCar's
`local_angle` mode): the dashboard still owns throttle (speed-capped), STOP,
and the heartbeat watchdog; the model only supplies steering while RUNNING.
This requires starting the process with a model:

```bash
python manage.py drive --model=models/mypilot.h5
```

If `DASHBOARD_USE_PILOT_STEERING` is enabled without `--model`, `manage.py`
raises an error at startup rather than silently driving straight.

## Raspberry Pi configuration

Uncomment and calibrate these values in `myconfig.py`:

```python
DASHBOARD_CONTROL_ENABLED = True
DASHBOARD_CONTROL_HOST = "0.0.0.0"
DASHBOARD_CONTROL_PORT = 9200
DASHBOARD_CONTROL_TOKEN = "replace-with-a-long-random-secret"
DASHBOARD_HEARTBEAT_TIMEOUT_S = 1.5
DASHBOARD_MAX_SPEED_MPS = 0.50
DASHBOARD_MAX_THROTTLE = 0.20
DASHBOARD_STRAIGHT_STEERING = 0.0
```

The token is mandatory when dashboard control is enabled. Configure the same
value as `DASHBOARD_ROVER_CONTROL_TOKEN` on the dashboard PC.

The Pi also serves the two-button standalone interface at
`http://RASPBERRY_PI_IP:9200/` from `WebInterface.html`.

Start the DonkeyCar process from this directory on the Raspberry Pi:

```bash
cd drive
python manage.py drive
```

Then configure the dashboard PC:

```dotenv
DASHBOARD_ROVER_CONTROL_URL=http://RASPBERRY_PI_IP:9200/api/control
DASHBOARD_ROVER_CONTROL_TOKEN=replace-with-a-long-random-secret
```

## Required hardware test order

1. Raise all driven wheels off the ground.
2. Calibrate `STEERING_LEFT_PWM`, `STEERING_RIGHT_PWM`, and the steering centre.
3. Verify `STOP` before trying `START`.
4. Begin with a much lower `DASHBOARD_MAX_THROTTLE` if `0.20` is not already
   known to be safe for this specific ESC/motor setup.
5. Disconnect Wi-Fi while running and confirm the heartbeat watchdog returns
   throttle to zero.
6. Only then perform a clear-floor, low-speed ground test.

If enabling `DASHBOARD_USE_PILOT_STEERING`, repeat steps 1, 3, 5, and 6 again
with the model loaded before a track test: pilot-supplied steering has not
been through the same wheels-raised verification as straight driving, and
`local_angle` mode still relies on the same STOP/heartbeat path for safety.

`target_speed_mps` is currently a calibrated command, not encoder-measured
speed. The uploaded PiRacer code has no wheel-speed feedback, so the dashboard
must not present it as a measured value.
