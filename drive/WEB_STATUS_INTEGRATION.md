# Dashboard LiDAR status integration

The drive team's existing files are not modified. Use the added entry point
instead of `manage.py` when the dashboard must distinguish a running command
from a LiDAR-blocked motor output.

On the drive Raspberry Pi:

```bash
cd ~/DIP_mobility_project/drive/drive_ver2
python web_manage_status.py drive --model models/mypilot.h5
```

For the latest stop-sign/GPIO build, use the equivalent added files in
`drive/drive_ver3`:

```bash
cd ~/DIP_mobility_project/drive/drive_ver3
python web_manage_status.py drive --model models/mypilot.h5
```

Use the real model path if it differs. The existing control endpoints remain
compatible:

- `POST :9200/api/control`
- `GET :9200/api/status`

The status response additionally contains:

```json
{
  "motion_state": "LIDAR_BLOCKED",
  "commanded_throttle": 0.315,
  "applied_throttle": 0.0,
  "drive_mode": "local_angle",
  "lidar_connected": true,
  "lidar_blocked": true,
  "lidar_nearest_m": 0.12
}
```

The integration never bypasses the LiDAR gate. In the ver2 wrapper it also
distinguishes a healthy scan with no forward return (open space) from a wholly
empty/failed scan. A failed scan still stops the vehicle, and a forward return
inside the configured stop distance still stops it immediately.
