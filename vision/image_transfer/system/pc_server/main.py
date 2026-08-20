import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
import routes_control
import routes_upload

app = FastAPI(title="VIS 통합 서버")

app.include_router(routes_upload.router)
app.include_router(routes_control.router)

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

os.makedirs(config.RECEIVED_DIR, exist_ok=True)
# 수신된 이미지를 웹 UI에서 <img src="/media/..."> 로 그대로 열람할 수 있게 정적 파일로 노출
app.mount("/media", StaticFiles(directory=config.RECEIVED_DIR), name="media")


def list_received_images() -> list[dict]:
    """수신된 이미지 목록 (최신순). design/README.md §5의 GET /images 데이터 소스."""
    images = []
    if not os.path.isdir(config.RECEIVED_DIR):
        return images

    for day in sorted(os.listdir(config.RECEIVED_DIR), reverse=True):
        day_dir = os.path.join(config.RECEIVED_DIR, day)
        if not os.path.isdir(day_dir):
            continue
        for filename in sorted(os.listdir(day_dir), reverse=True):
            if not filename.lower().endswith(".jpg"):
                continue
            rel_path = f"{day}/{filename}"
            images.append(
                {
                    "rel_path": rel_path,
                    "filename": filename,
                    "day": day,
                    "url": f"/media/{rel_path}",
                }
            )
    return images


@app.get("/images")
def api_images():
    """이미지 목록 JSON (대시보드 새로고침용)."""
    return {"images": list_received_images()}


class DeleteImagesRequest(BaseModel):
    paths: list[str]  # RECEIVED_DIR 기준 상대경로 목록 (예: "2026-08-20/20260820_170305_cam01_001.jpg")


@app.post("/images/delete")
def delete_images(req: DeleteImagesRequest):
    """PC received/ 보관소에서 선택한 이미지를 삭제.

    라즈베리파이 로컬 저장소와는 무관 — PC가 이미 갖고 있는 사본만 지운다.
    """
    deleted = []
    rejected = []

    for rel_path in req.paths:
        # 경로 조작(../ 등) 방지: RECEIVED_DIR 바깥으로 못 벗어나게 한다
        full_path = os.path.normpath(os.path.join(config.RECEIVED_DIR, rel_path))
        if not full_path.startswith(os.path.normpath(config.RECEIVED_DIR) + os.sep):
            rejected.append({"path": rel_path, "reason": "invalid_path"})
            continue
        try:
            os.remove(full_path)
            deleted.append(rel_path)
        except OSError as e:
            rejected.append({"path": rel_path, "reason": f"delete_error: {e}"})

    return {"deleted": deleted, "rejected": rejected}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    images = list_received_images()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "images": images,
            "received_count": len(images),
            "config_dir": "received/",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT)
