# Drive / dashboard control changelog

Tracks changes to the `drive/` DonkeyCar project and its dashboard control
integration, since these are deployed by hand onto the PiRacer's `~/mycar`
(a separate, unversioned-here checkout — see "Deployment" below) rather than
pulled via git on the Pi itself.

## Unreleased (working tree, not yet committed)

### Added
- `DASHBOARD_USE_PILOT_STEERING` config flag (`config.py`, `myconfig.py`).
  When `True`, `START` hands steering to the trained pilot model via
  DonkeyCar's `local_angle` mode instead of driving straight; the dashboard
  still owns throttle (speed-capped), `STOP`, and the heartbeat watchdog.
  Requires `manage.py drive --model=<path to .h5>` — `manage.py` now raises
  at startup if the flag is set without a model.
  (`drive/dashboard_control.py`, `drive/manage.py`)
- Tests for the new mode: default straight-driving behavior is unchanged,
  `local_angle` is only used while RUNNING, and STOP still forces plain
  `user` mode even with pilot steering enabled. (`tests/test_dashboard_control.py`)

### Fixed
- `WebInterface.html`'s START/STOP buttons silently did nothing when served
  over plain `http://<pi-ip>:9200/` (not HTTPS/localhost): `crypto.randomUUID()`
  is only available in secure contexts, so it threw before the fetch to
  `/api/control` was ever made, with no visible error. Replaced with a
  `generateCommandId()` fallback that works in any context.

### Deployment notes
- `~/mycar` on the Pi tracks a different, older GitHub repo
  (`genie0000/PiRacer_AI`, `main` only) than this one
  (`sangjin-mo/DIP_mobility_project`, now the main repo) and has its own
  uncommitted hardware calibration (`STEERING_LEFT_PWM=400`,
  `STEERING_RIGHT_PWM=660`, `BATCH_SIZE=16`). Changes here are grafted onto
  `~/mycar` file-by-file rather than via `git pull`, to avoid clobbering that
  calibration. `.bak` copies of the pre-graft `config.py`/`manage.py`/
  `myconfig.py` were left on the Pi.
- `~/mycar/myconfig.py` on the Pi has `DASHBOARD_CONTROL_ENABLED = True`,
  `DASHBOARD_USE_PILOT_STEERING = True`, and a generated
  `DASHBOARD_CONTROL_TOKEN` that is not committed anywhere in this repo.

## 0b1e1b4 — Add web-controlled PiRacer start and stop

- `drive/dashboard_control.py`: `DashboardControlPart`, a DonkeyCar-agnostic
  threaded part that runs its own small HTTP server (`/api/control`,
  `/api/status`, and serving `WebInterface.html` at `/`) in the same process
  as the vehicle loop. Supports `START`/`STOP`/`HEARTBEAT`, bearer-token
  auth, and a heartbeat watchdog that force-stops on missed pings.
- `drive/manage.py`: wires `DashboardControlPart` in ahead of the throttle
  filter, overriding `LocalWebController`'s `user/angle`/`user/throttle`/
  `user/mode` outputs whenever `DASHBOARD_CONTROL_ENABLED` is set.
- `drive/WebInterface.html`: two-button (start/stop) standalone control page.
- `drive/README.md`: Pi-side config and the required hardware test order
  (wheels raised, calibrate steering, verify STOP, low throttle, Wi-Fi-drop
  watchdog check, only then a ground test).

## 40aa77e / 5b1d3a6 — Add PiRacer driving source files

Generic DonkeyCar `mycar` scaffold (`manage.py`, `config.py`, `myconfig.py`,
`train.py`) uploaded as the starting point for this project's drive code.
