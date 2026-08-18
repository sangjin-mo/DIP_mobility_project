"""Minimal executable entry point for the dashboard process."""

from __future__ import annotations

import argparse

import uvicorn

from web_dashboard.app import create_app
from web_dashboard.config import get_dashboard_settings


def main(argv: list[str] | None = None) -> int:
    settings = get_dashboard_settings()

    parser = argparse.ArgumentParser(prog="python -m web_dashboard")
    parser.add_argument("--host", default=settings.HOST)
    parser.add_argument("--port", type=int, default=settings.PORT)
    args = parser.parse_args(argv)

    uvicorn.run(create_app(dashboard_settings=settings), host=args.host, port=args.port)
    return 0
