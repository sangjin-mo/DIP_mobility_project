import glob
import hashlib
import json
import os
from datetime import datetime

import requests

import config


def sha256_of_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_upload_status() -> dict:
    if not os.path.exists(config.UPLOAD_STATUS_FILE):
        return {}
    with open(config.UPLOAD_STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_upload_status(status: dict):
    with open(config.UPLOAD_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def to_rel_path(filepath: str) -> str:
    """upload_status.json의 키이자 PC/웹 UI와 주고받는 경로 형식.

    os.path.relpath는 OS에 따라 구분자가 "\\"(Windows) / "/"(Linux)로 갈리는데,
    이 값이 그대로 API(/delete-local 등)를 오가는 식별자로 쓰이므로 항상 "/"로 고정한다.
    """
    return os.path.relpath(filepath, config.BASE_DIR).replace(os.sep, "/")


def snapshot_pending_files(status: dict) -> list:
    """지금 이 순간 로컬에 저장된 이미지 중, 아직 전송 성공 기록이 없는 파일 목록."""
    all_files = sorted(glob.glob(os.path.join(config.BASE_DIR, "*", "*.jpg")))
    pending = []
    for filepath in all_files:
        rel_path = to_rel_path(filepath)
        if status.get(rel_path, {}).get("state") != "success":
            pending.append(filepath)
    return pending


def upload_one(filepath: str) -> dict:
    filename = os.path.basename(filepath)
    checksum = sha256_of_file(filepath)

    try:
        with open(filepath, "rb") as f:
            response = requests.post(
                f"{config.SERVER_URL}/upload",
                files={"file": (filename, f, "image/jpeg")},
                data={"checksum": checksum},
                timeout=config.UPLOAD_TIMEOUT_SEC,
            )
    except requests.exceptions.RequestException as e:
        return {"state": "failed", "reason": f"connection_error: {e}"}

    if response.status_code == 200:
        return {"state": "success", "checksum": checksum, "uploaded_at": datetime.now().isoformat()}

    return {"state": "failed", "reason": f"server_rejected: {response.status_code} {response.text}"}


def main():
    status = load_upload_status()
    pending = snapshot_pending_files(status)

    if not pending:
        print("전송할 새 이미지가 없습니다.")
        return

    print(f"전송 대상: {len(pending)}장 (오더 시점 스냅샷 기준)")

    success_count = 0
    failed_count = 0

    for filepath in pending:
        rel_path = to_rel_path(filepath)
        result = upload_one(filepath)
        status[rel_path] = result
        save_upload_status(status)  # 파일 하나씩 바로 기록 (중간에 끊겨도 진행 상황 보존)

        if result["state"] == "success":
            success_count += 1
            print(f"성공: {rel_path}")
        else:
            failed_count += 1
            print(f"실패: {rel_path} ({result['reason']})")

    print(f"\n완료. 성공 {success_count}장 / 실패 {failed_count}장")
    if failed_count > 0:
        print("실패한 파일은 삭제되지 않았습니다. 다음 오더 때 자동으로 다시 전송됩니다.")


if __name__ == "__main__":
    main()
