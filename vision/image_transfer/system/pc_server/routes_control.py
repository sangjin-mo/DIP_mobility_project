import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config

router = APIRouter(prefix="/control")


@router.post("/request-transfer")
def request_transfer():
    """웹 UI의 "전송 요청" 버튼 → 라즈베리파이의 /trigger-upload를 대신 호출.

    design/README.md §4-2 시퀀스. 동기 방식(라즈베리파이_전환_구현계획.md 결정 그대로)이라
    라즈베리파이가 전송을 다 마칠 때까지 이 요청도 같이 대기한다.
    """
    try:
        response = requests.post(
            f"{config.PI_TRIGGER_URL}/trigger-upload",
            timeout=config.PI_REQUEST_TIMEOUT_SEC,
        )
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "reason": f"pi_unreachable: {e}"},
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "reason": f"pi_rejected: {response.status_code} {response.text}"},
        )

    return response.json()


class DeleteLocalRequest(BaseModel):
    paths: list[str]  # 라즈베리파이 BASE_DIR 기준 상대경로 목록


@router.post("/delete-local")
def delete_local(req: DeleteLocalRequest):
    """웹 UI의 "선택 항목 로컬 삭제" 버튼 → 라즈베리파이의 /delete-local을 대신 호출.

    design/README.md §4-3 시퀀스. 최종 검증(전송 성공 여부)은 라즈베리파이 쪽에서
    upload_status.json 기준으로 다시 한다 — 여기서는 그대로 전달만 한다.
    """
    try:
        response = requests.post(
            f"{config.PI_TRIGGER_URL}/delete-local",
            json={"paths": req.paths},
            timeout=config.PI_REQUEST_TIMEOUT_SEC,
        )
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "reason": f"pi_unreachable: {e}"},
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "reason": f"pi_rejected: {response.status_code} {response.text}"},
        )

    return response.json()


@router.post("/delete-all-local")
def delete_all_local():
    """웹 UI의 "로컬 저장소 전체 삭제" 버튼 → 라즈베리파이의 /delete-all-local을 대신 호출.

    라즈베리파이 로컬 저장소(용량 관리 대상)를 통째로 비운다. PC의 received/ 이미지와는
    무관 — PC 쪽 이미지를 지우려면 /images/delete(선택 삭제)를 쓴다.
    """
    try:
        response = requests.post(
            f"{config.PI_TRIGGER_URL}/delete-all-local",
            timeout=config.PI_REQUEST_TIMEOUT_SEC,
        )
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "reason": f"pi_unreachable: {e}"},
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "reason": f"pi_rejected: {response.status_code} {response.text}"},
        )

    return response.json()
