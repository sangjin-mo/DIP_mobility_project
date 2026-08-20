import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import upload_batch

app = FastAPI()


@app.middleware("http")
async def restrict_to_allowed_host(request: Request, call_next):
    allowed = config.TRIGGER_ALLOWED_HOST
    if allowed and request.client.host != allowed:
        return JSONResponse(
            status_code=403,
            content={"status": "error", "reason": "forbidden_host", "client": request.client.host},
        )
    return await call_next(request)


@app.post("/trigger-upload")
def trigger_upload():
    status = upload_batch.load_upload_status()
    pending = upload_batch.snapshot_pending_files(status)

    success_count = 0
    failed_count = 0

    for filepath in pending:
        rel_path = upload_batch.to_rel_path(filepath)
        result = upload_batch.upload_one(filepath)
        status[rel_path] = result
        upload_batch.save_upload_status(status)  # 파일 하나씩 바로 기록 (중간에 끊겨도 진행 상황 보존)

        if result["state"] == "success":
            success_count += 1
        else:
            failed_count += 1

    return {
        "requested": len(pending),
        "success": success_count,
        "failed": failed_count,
    }


class DeleteLocalRequest(BaseModel):
    paths: list[str]  # config.BASE_DIR 기준 상대경로 (예: "2026-08-20/20260820_170305_cam01_001.jpg")


@app.post("/delete-local")
def delete_local(req: DeleteLocalRequest):
    """전송 성공(success)이 확인된 파일만 로컬에서 삭제.

    design/README.md §4-3, §8의 안전장치: 전송되지 않은 파일은 여기서 거부한다.
    PC 쪽(웹 UI)에서도 한 번 걸러서 보내지만, 최종 검증은 실제 파일을 갖고 있는
    라즈베리파이 쪽에서 다시 한다.
    """
    status = upload_batch.load_upload_status()
    deleted = []
    rejected = []

    for rel_path in req.paths:
        if status.get(rel_path, {}).get("state") != "success":
            rejected.append({"path": rel_path, "reason": "not_uploaded"})
            continue

        full_path = os.path.join(config.BASE_DIR, rel_path)
        try:
            os.remove(full_path)
            deleted.append(rel_path)
        except OSError as e:
            rejected.append({"path": rel_path, "reason": f"delete_error: {e}"})

    return {"deleted": deleted, "rejected": rejected}


@app.post("/delete-all-local")
def delete_all_local():
    """로컬 저장소 데이터 전체 삭제 (용량 관리용).

    전송 성공(success)이 확인된 파일만 지운다 — 아직 PC로 안 넘어간 파일까지
    같이 날리면 사진이 영구 유실되므로, "전체 삭제"라도 이 안전장치는 유지한다
    (design/README.md §4-3 안전장치와 동일한 원칙).
    """
    status = upload_batch.load_upload_status()
    deleted = []
    already_missing = []

    for rel_path, info in status.items():
        if info.get("state") != "success":
            continue
        full_path = os.path.join(config.BASE_DIR, rel_path)
        try:
            os.remove(full_path)
            deleted.append(rel_path)
        except FileNotFoundError:
            already_missing.append(rel_path)

    pending = upload_batch.snapshot_pending_files(status)

    return {
        "deleted": deleted,
        "already_missing": already_missing,
        "pending_kept": len(pending),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.TRIGGER_PORT)
