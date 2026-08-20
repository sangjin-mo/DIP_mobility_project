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

The web control feature is isolated in files added by this project:

- `web_manage.py`: a separate executable entry point which reuses
  `manage.drive()` without editing `manage.py` or any original config file.
- `dashboard_control.py`: authenticated command API and heartbeat watchdog.
- `WebInterface.html`: standalone two-button browser interface.

The command API runs in the same process that owns the DonkeyCar actuators:

- `START`: maps the requested target speed linearly onto a calibrated maximum
  throttle and drives with the configured straight steering value.
- `STOP`: the single user-facing safety stop; immediately outputs neutral
  steering and zero throttle.
- `HEARTBEAT`: keeps a running command alive. Missing heartbeats force a local
  stop even if the PC, browser, or Wi-Fi fails.

This first version is deliberately straight-driving only. It does not claim to
follow a greenhouse route without a vision/line-following model.

## Raspberry Pi web-control execution

No original drive/config file needs to be edited for the web feature. Set the
token in the Raspberry Pi process environment and run the new entry point:

```bash
cd drive
export DASHBOARD_CONTROL_TOKEN='replace-with-a-long-random-secret'
python web_manage.py --max-speed 0.50 --max-throttle 0.20
```

`--max-throttle` and `--straight-steering` must be calibrated for the real
vehicle. Run `python web_manage.py --help` for every available option. Configure
the same token as `DASHBOARD_ROVER_CONTROL_TOKEN` on the dashboard PC.

The Pi also serves the two-button standalone interface at
`http://RASPBERRY_PI_IP:9200/` from `WebInterface.html`.

Configure the dashboard PC:

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

`target_speed_mps` is currently a calibrated command, not encoder-measured
speed. The uploaded PiRacer code has no wheel-speed feedback, so the dashboard
must not present it as a measured value.
