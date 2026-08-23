"""Read-only dashboard view over reports produced by the AI/LLM pipeline."""

from __future__ import annotations

from web_dashboard.services.report_service import ReportNotFoundError, ReportService


class CropReportService:
    def __init__(self, reports: ReportService) -> None:
        self._reports = reports

    def latest(self) -> dict:
        for metadata in self._reports.list_reports():
            try:
                return self.get(metadata["patrol_id"])
            except ReportNotFoundError:
                continue
        return {
            "available": False,
            "patrol_id": None,
            "generated_at": None,
            "overall_status": None,
            "llm_enabled": False,
            "zones": [],
            "report_markdown": None,
        }

    def get(self, patrol_id: str) -> dict:
        metadata = self._reports.metadata(patrol_id)
        markdown = self._reports.markdown(patrol_id)
        zones = []
        for index, zone in enumerate(metadata.get("zones", [])):
            zones.append(
                {
                    "label": chr(ord("A") + index) if index < 26 else str(index + 1),
                    "zone_id": zone.get("zone_id"),
                    "zone_name": zone.get("zone_name"),
                    "status": zone.get("status"),
                    "observations": zone.get("observations", {}),
                    "confidence": zone.get("confidence"),
                }
            )
        return {
            "available": True,
            "patrol_id": patrol_id,
            "generated_at": metadata.get("generated_at"),
            "overall_status": metadata.get("overall_status"),
            "llm_enabled": metadata.get("llm", {}).get("enabled", False),
            "zones": zones,
            "report_markdown": markdown,
        }
