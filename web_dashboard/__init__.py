"""Lightweight WEB dashboard that integrates with the existing AI subsystem.

This package is intentionally separate from ``ai_report``.  It consumes the
existing configuration, boundary models, SQLite layout, and report directory
without replacing the original ingest or report-generation code.
"""

from web_dashboard.app import create_app

__all__ = ["create_app"]
