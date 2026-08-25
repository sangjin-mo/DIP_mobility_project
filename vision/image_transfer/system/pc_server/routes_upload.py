import hashlib
import os
import re
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

import config

router = APIRouter()

# 파일명 규칙: 20260815_143205_cam01_001.jpg -> 앞 8자리가 날짜
FILENAME_DATE_RE = re.compile(r"^(\d{8})_")


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FILENAME_STAMP_RE = re.compile(r"^(\d{8}_\d{6})_")


def restore_capture_mtime(save_path: str, filename: str) -> None:
    """파일 mtime을 업로드 시각이 아니라 촬영 시각으로 되돌린다.

    업로드는 촬영보다 한참 뒤(때로는 순찰이 끝난 뒤)에 일어나므로, 그냥 쓰면
    한 배치의 모든 파일이 "PC에 도착한 시각"이라는 똑같은 mtime을 갖게 된다.
    촬영 시각은 파일명에만 남아 있는데, 파일명을 파싱하는 코드가 늘어날수록
    형식이 어긋날 위험도 커진다. 여기서 한 번 되돌려 두면 mtime을 보는 모든
    소비자(이미지 목록 정렬, 오래된 파일 정리 등)가 자동으로 맞는 값을 본다.

    실패해도 업로드 자체는 성공으로 처리한다 — mtime은 부가 정보이고,
    촬영 시각의 1차 출처는 여전히 파일명이다.
    """
    match = FILENAME_STAMP_RE.match(filename)
    if not match:
        return
    try:
        # DTZ007을 의도적으로 무시한다: 파일명 스탬프에는 타임존이 없고,
        # 라즈베리파이가 로컬 시각으로 기록하므로 로컬 시각 해석이 맞다.
        captured = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").timestamp()  # noqa: DTZ007
        os.utime(save_path, (captured, captured))
    except (ValueError, OSError):
        return


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

    restore_capture_mtime(save_path, file.filename)

    return {"status": "ok", "filename": file.filename, "checksum": actual_checksum}
