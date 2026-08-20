import hashlib
import os
import re

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

import config

router = APIRouter()

# 파일명 규칙: 20260815_143205_cam01_001.jpg -> 앞 8자리가 날짜
FILENAME_DATE_RE = re.compile(r"^(\d{8})_")


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def day_dir_from_filename(filename: str) -> str:
    match = FILENAME_DATE_RE.match(filename)
    if not match:
        raise ValueError(f"파일명에서 날짜를 찾을 수 없음: {filename}")
    date_str = match.group(1)
    return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"


@router.post("/upload")
async def upload(file: UploadFile = File(...), checksum: str = Form(...)):
    """라즈베리파이의 upload_batch.py가 호출하는 수신 엔드포인트.

    design/README.md §4-2, §5 참고. webcam_test/server.py의 검증 로직을 그대로 재사용.
    """
    data = await file.read()
    actual_checksum = sha256_of_bytes(data)

    if actual_checksum != checksum:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "reason": "checksum_mismatch",
                "expected": checksum,
                "actual": actual_checksum,
            },
        )

    try:
        day_dir = day_dir_from_filename(file.filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "reason": str(e)})

    save_dir = os.path.join(config.RECEIVED_DIR, day_dir)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, file.filename)

    with open(save_path, "wb") as f:
        f.write(data)

    return {"status": "ok", "filename": file.filename, "checksum": actual_checksum}
