# PiRacer drive: web + Vision stop sign + YDLiDAR X2

`4_drive_final_0821` is intentionally unchanged. This directory is its
independent successor: the original web START/STOP and heartbeat controller
remain in `dashboard_control.py`, with two added safety paths.

## Safety behaviour

| Event | Drive Pi result | Resume |
| --- | --- | --- |
| Web STOP or Vision stop sign | Dashboard state becomes `STOPPED`; throttle is zero. | Press web START again. |
| Browser heartbeat missing | Dashboard watchdog stops the car. | Restore the browser connection and press START. |
| LiDAR range <= 0.15 m (safety margin for 10 cm) | Final throttle is zero; dashboard remains `RUNNING`. | Automatic, after 3 clear scans and active heartbeat. |
| LiDAR missing, scan failure, or Python driver unavailable | Final throttle is zero (`LIDAR_FAIL_SAFE_STOP=True`). | Fix the LiDAR/driver, then clear scans release the gate. |

The safety decision uses a 30-degree forward sector: centre angle
`LIDAR_FORWARD_CENTER_DEG` plus/minus `LIDAR_FORWARD_HALF_ANGLE_DEG` (15° by
default). The LiDAR gates throttle only and never reads or writes steering;
line-following steering always stays with the trained pilot model. The X2's minimum range is approximately 10–12
cm; `0.15 m` is deliberately used as a safety margin so the car stops before
an obstacle reaches 10 cm.

The YDLiDAR SDK runs in a separate process. Only its `blocked` status is sent
back to the DonkeyCar process, so a blocking scan cannot delay the camera or
pilot-model loop.

## Drive Raspberry Pi setup

1. Copy this whole directory to the drive Raspberry Pi and retain its existing
   DonkeyCar environment.
2. Install the vendor-supported YDLiDAR Python SDK that exposes `import ydlidar`.
   The X2 is configured for `/dev/ttyUSB0` at 115200 baud.
3. Ensure the `pi` user can access the serial port (normally add it to the
   `dialout` group), then reconnect/login.
4. In `myconfig.py`, confirm the LiDAR settings and calibrate the forward axis.
5. Start the car exactly as before, including the trained pilot model:

```bash
cd ~/mycar
python manage.py drive --model models/mypilot.h5
```

With `LIDAR_SAFETY_ENABLED=True`, a driver/USB error deliberately prevents
motion. Never set `LIDAR_FAIL_SAFE_STOP=False` for normal autonomous driving.

## Vision Raspberry Pi setup

The Vision Pi runs `vision_stop_detector.py`. It uses Ultralytics YOLO's COCO
`stop sign` class, requires three consecutive detections by default, then sends
one authenticated `STOP` to the drive Pi. It does not need DonkeyCar.

```bash
pip install ultralytics opencv-python
export PIRACER_CONTROL_URL=http://DRIVE_PI_IP:9200/api/control
export PIRACER_CONTROL_TOKEN='the same dashboard token configured on the drive Pi'
python vision_stop_detector.py --camera 0
```

For an offline Vision Pi, copy the model file there first and run, for example,
`python vision_stop_detector.py --model /home/pi/models/yolo11n.pt`.

## Required test sequence

1. Raise all driven wheels off the floor.
2. Disconnect the LiDAR: verify web START leaves throttle at zero.
3. Reconnect it and verify clear scans permit motion.
4. Place an object inside the 10 cm forward sector: verify immediate stop.
5. Remove the object: verify automatic resumption only while the webpage's
   heartbeat is still active.
6. On the Vision Pi, show a stop sign and verify the car remains stopped until
   the web START button is deliberately pressed again.
