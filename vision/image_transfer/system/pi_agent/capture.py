import os
import shutil
import time
from datetime import datetime

import cv2

import config


def make_filename(dt: datetime, cam_id: str, seq: int) -> str:
    return f"{dt.strftime('%Y%m%d_%H%M%S')}_{cam_id}_{seq:03d}.jpg"


def get_day_dir(dt: datetime) -> str:
    day_dir = os.path.join(config.BASE_DIR, dt.strftime("%Y-%m-%d"))
    os.makedirs(day_dir, exist_ok=True)
    return day_dir


def save_image(filepath: str, frame) -> bool:
    # cv2.imwrite silently fails on Windows when the path contains non-ASCII
    # characters (e.g. Korean folder names), so encode in memory and write
    # the bytes ourselves instead.
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return False
    buf.tofile(filepath)
    return True


def free_space_mb(path: str) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 * 1024)


def ensure_disk_space():
    """여유 공간이 부족하면 가장 오래된 날짜 폴더부터 삭제."""
    while free_space_mb(config.BASE_DIR) < config.MIN_FREE_DISK_MB:
        day_dirs = sorted(
            d for d in os.listdir(config.BASE_DIR)
            if os.path.isdir(os.path.join(config.BASE_DIR, d))
        )
        if not day_dirs:
            print("저장 공간 부족하지만 삭제할 폴더가 없음", flush=True)
            break

        oldest = os.path.join(config.BASE_DIR, day_dirs[0])
        shutil.rmtree(oldest)
        print(f"저장 공간 부족으로 오래된 폴더 삭제: {oldest}", flush=True)


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"카메라를 열 수 없습니다 (index={index})")
    return cap


def reconnect_camera(cap: cv2.VideoCapture) -> cv2.VideoCapture:
    """카메라 재연결을 계속 시도. 성공할 때까지 블로킹."""
    cap.release()
    while True:
        print("카메라 재연결 시도 중...", flush=True)
        try:
            return open_camera(config.CAMERA_INDEX)
        except RuntimeError:
            time.sleep(config.RECONNECT_RETRY_DELAY_SEC)


def main():
    os.makedirs(config.BASE_DIR, exist_ok=True)
    cap = open_camera(config.CAMERA_INDEX)

    total_saved = 0
    last_second = None
    seq = 0
    consecutive_failures = 0
    last_disk_check = 0.0

    try:
        while True:
            ret, frame = cap.read()

            if not ret:
                consecutive_failures += 1
                print(f"프레임 획득 실패 ({consecutive_failures}/{config.MAX_CONSECUTIVE_FAILURES})", flush=True)
                if consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
                    cap = reconnect_camera(cap)
                    consecutive_failures = 0
                continue

            consecutive_failures = 0
            now = datetime.now()

            now_ts = time.monotonic()
            if now_ts - last_disk_check >= config.DISK_CHECK_INTERVAL_SEC:
                ensure_disk_space()
                last_disk_check = now_ts

            current_second = now.strftime("%Y%m%d_%H%M%S")

            if current_second == last_second:
                seq += 1
            else:
                seq = 1
                last_second = current_second

            day_dir = get_day_dir(now)
            filename = make_filename(now, config.CAMERA_ID, seq)
            filepath = os.path.join(day_dir, filename)

            try:
                saved = save_image(filepath, frame)
            except OSError as e:
                print(f"저장 실패 (디스크 오류): {filepath} ({e})", flush=True)
                continue

            if not saved:
                print(f"저장 실패: {filepath}", flush=True)
                continue

            total_saved += 1
            print(f"저장: {filepath}", flush=True)

            time.sleep(config.CAPTURE_INTERVAL_SEC)

    except KeyboardInterrupt:
        print(f"\n종료. 총 {total_saved}장 저장됨.", flush=True)

    finally:
        cap.release()


if __name__ == "__main__":
    main()
