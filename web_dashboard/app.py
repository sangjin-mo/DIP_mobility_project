"""FastAPI composition root for the lightweight dashboard.

Routes delegate data access to small services.  No telemetry parsing,
aggregation, LLM calls, or report calculations are duplicated here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

from ai_report.config import Settings, get_settings
from web_dashboard.config import DashboardSettings, get_dashboard_settings
from web_dashboard.services.control_service import (
    ControlCommandError,
    ControlUnavailableError,
    DriveCommand,
    RoverControlService,
)
from web_dashboard.services.crop_report_service import CropReportService
from web_dashboard.services.live_service import LiveStateService
from web_dashboard.services.report_service import (
    InvalidReportError,
    ReportNotFoundError,
    ReportService,
)
from web_dashboard.services.vision_service import (
    VisionCaptureService,
    VisionResponseError,
    VisionUnavailableError,
)
from web_dashboard.services.weather_service import KmaWeatherService, WeatherUnavailableError

PACKAGE_ROOT = Path(__file__).resolve().parent


class StartRequest(BaseModel):
    target_speed_mps: float | None = Field(default=None, gt=0, le=1.0)


def create_app(
    ai_settings: Settings | None = None,
    dashboard_settings: DashboardSettings | None = None,
) -> FastAPI:
    ai_config = ai_settings or get_settings()
    web_config = dashboard_settings or get_dashboard_settings()
    reports = ReportService(ai_config.REPORT_ROOT)
    crop_reports = CropReportService(reports)
    live = LiveStateService(ai_config.sqlite_path, web_config.TELEMETRY_STALE_AFTER_S)
    control = RoverControlService(
        web_config.ROVER_CONTROL_URL,
        timeout_s=web_config.CONTROL_TIMEOUT_S,
        token=web_config.ROVER_CONTROL_TOKEN,
        status_url=web_config.ROVER_STATUS_URL,
    )
    weather = KmaWeatherService(
        web_config.KMA_SERVICE_KEY,
        web_config.KMA_NX,
        web_config.KMA_NY,
        location_label=web_config.WEATHER_LOCATION_LABEL,
        refresh_interval_minutes=web_config.WEATHER_REFRESH_INTERVAL_MINUTES,
        timeout_s=web_config.WEATHER_TIMEOUT_S,
    )
    vision = VisionCaptureService(
        web_config.VISION_SERVER_URL,
        timeout_s=web_config.VISION_TIMEOUT_S,
        max_image_bytes=web_config.VISION_MAX_IMAGE_BYTES,
    )

    templates = Environment(
        loader=FileSystemLoader(PACKAGE_ROOT / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )

    app = FastAPI(title="Mobility Patrol Dashboard")
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        template = templates.get_template("dashboard.html")
        return HTMLResponse(template.render(camera_url=web_config.CAMERA_URL))

    @app.get("/api/status")
    async def status() -> dict:
        return {
            "service": "web_dashboard",
            "data_root": str(ai_config.DATA_ROOT),
            "report_root": str(ai_config.REPORT_ROOT),
            "database_exists": ai_config.sqlite_path.is_file(),
            "camera_configured": vision.configured or bool(web_config.CAMERA_URL),
            "vision_capture_configured": vision.configured,
            "control_configured": control.configured,
            "weather_configured": weather.configured,
            "weather_refresh_interval_s": web_config.WEATHER_REFRESH_INTERVAL_MINUTES * 60,
            "default_target_speed_mps": web_config.DEFAULT_TARGET_SPEED_MPS,
            "max_target_speed_mps": web_config.MAX_TARGET_SPEED_MPS,
        }

    @app.get("/api/weather")
    async def current_weather() -> dict:
        try:
            return await asyncio.to_thread(weather.get)
        except WeatherUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/weather/refresh")
    async def refresh_weather() -> dict:
        try:
            return await asyncio.to_thread(weather.get, True)
        except WeatherUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/live/latest")
    async def live_latest() -> dict:
        return await asyncio.to_thread(live.snapshot)

    @app.websocket("/ws/live")
    async def live_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(await asyncio.to_thread(live.snapshot))
                await asyncio.sleep(web_config.LIVE_POLL_INTERVAL_S)
        except WebSocketDisconnect:
            return

    @app.post("/api/control/start")
    async def start_rover(request: StartRequest) -> dict:
        speed = request.target_speed_mps or web_config.DEFAULT_TARGET_SPEED_MPS
        if speed > web_config.MAX_TARGET_SPEED_MPS:
            raise HTTPException(
                status_code=422,
                detail=f"목표 속도는 {web_config.MAX_TARGET_SPEED_MPS:.2f} m/s 이하여야 합니다.",
            )
        return await _control_call(control, DriveCommand.START, speed)

    @app.post("/api/control/stop")
    async def stop_rover() -> dict:
        return await _control_call(control, DriveCommand.STOP)

    @app.post("/api/control/heartbeat")
    async def heartbeat_rover() -> dict:
        return await _control_call(control, DriveCommand.HEARTBEAT)

    @app.get("/api/control/status")
    async def rover_status() -> dict:
        try:
            return await asyncio.to_thread(control.status)
        except ControlUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ControlCommandError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/camera/latest")
    async def latest_camera_image() -> dict:
        try:
            latest = await asyncio.to_thread(vision.latest)
        except (VisionUnavailableError, VisionResponseError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"available": latest is not None, "image": latest}

    @app.post("/api/camera/capture")
    async def capture_camera_image() -> dict:
        try:
            latest = await asyncio.to_thread(vision.capture)
        except VisionUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except VisionResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"captured": True, "image": latest}

    @app.get("/api/camera/latest-image")
    async def latest_camera_image_bytes() -> Response:
        try:
            data, content_type = await asyncio.to_thread(vision.latest_image)
        except VisionUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except VisionResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=data, media_type=content_type, headers={"Cache-Control": "no-store"})

    @app.get("/api/crop-report/latest")
    async def latest_crop_report() -> dict:
        return await asyncio.to_thread(crop_reports.latest)

    @app.get("/api/crop-report/{patrol_id}")
    async def crop_report(patrol_id: str) -> dict:
        return await _service_call(crop_reports.get, patrol_id)

    @app.post("/api/crop-report/generate")
    async def generate_crop_report() -> dict:
        available = await asyncio.to_thread(reports.list_reports)
        if not available:
            raise HTTPException(status_code=409, detail="생성할 순찰 기록이 없습니다.")
        patrol_id = available[0]["patrol_id"]
        try:
            from ai_report.cli import _regenerate

            await _regenerate(patrol_id, ai_config.REPORT_ROOT)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=409,
                detail="저장된 payload.json이 없어 레포트를 다시 생성할 수 없습니다.",
            ) from exc
        return await asyncio.to_thread(crop_reports.get, patrol_id)

    @app.get("/api/patrols")
    async def patrols() -> list[dict]:
        return await asyncio.to_thread(reports.list_reports)

    @app.get("/api/patrols/{patrol_id}")
    async def patrol_metadata(patrol_id: str) -> dict:
        return await _service_call(reports.metadata, patrol_id)

    @app.get("/api/patrols/{patrol_id}/report", response_class=PlainTextResponse)
    async def patrol_report(patrol_id: str) -> PlainTextResponse:
        text = await _service_call(reports.markdown, patrol_id)
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")

    @app.get("/api/patrols/{patrol_id}/images/{image_id}")
    async def patrol_image(patrol_id: str, image_id: str) -> FileResponse:
        path = await _service_call(reports.image, patrol_id, image_id)
        return FileResponse(path, media_type="image/jpeg")

    return app


async def _service_call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except InvalidReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _control_call(
    control: RoverControlService,
    command: DriveCommand,
    target_speed_mps: float | None = None,
) -> dict:
    try:
        return await asyncio.to_thread(control.send, command, target_speed_mps)
    except ControlUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ControlCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
