"""FastAPI composition root for the lightweight dashboard.

Routes delegate data access to small services.  No telemetry parsing,
aggregation, LLM calls, or report calculations are duplicated here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ai_report.config import Settings, get_settings
from web_dashboard.config import DashboardSettings, get_dashboard_settings
from web_dashboard.services.live_service import LiveStateService
from web_dashboard.services.report_service import (
    InvalidReportError,
    ReportNotFoundError,
    ReportService,
)

PACKAGE_ROOT = Path(__file__).resolve().parent


def create_app(
    ai_settings: Settings | None = None,
    dashboard_settings: DashboardSettings | None = None,
) -> FastAPI:
    ai_config = ai_settings or get_settings()
    web_config = dashboard_settings or get_dashboard_settings()
    reports = ReportService(ai_config.REPORT_ROOT)
    live = LiveStateService(ai_config.sqlite_path, web_config.TELEMETRY_STALE_AFTER_S)

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
            "camera_configured": bool(web_config.CAMERA_URL),
            "control_connected": False,
        }

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
