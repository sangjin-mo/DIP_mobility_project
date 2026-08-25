"""Read reports produced by the existing AI storage pipeline."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from ai_report.models import PATROL_ID_PATTERN, PatrolAggregate

_IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class ReportNotFoundError(FileNotFoundError):
    pass


class InvalidReportError(ValueError):
    pass


class ReportService:
    def __init__(self, report_root: Path) -> None:
        self._report_root = Path(report_root)

    def list_patrol_ids(self) -> list[str]:
        """Patrol ids with a report directory, newest first — names only.

        Cheap: one `iterdir` and a regex per entry, no file reads. Callers
        that only need the newest renderable report (see
        `CropReportService.latest`) should walk this and stop at the first
        one that loads, rather than making `list_reports()` parse and
        validate every report on disk first.
        """
        if not self._report_root.is_dir():
            return []
        return sorted(
            (
                directory.name
                for directory in self._report_root.iterdir()
                if directory.is_dir() and PATROL_ID_PATTERN.fullmatch(directory.name)
            ),
            reverse=True,
        )

    def list_reports(self) -> list[dict]:
        reports: list[dict] = []
        for patrol_id in self.list_patrol_ids():
            try:
                reports.append(self.metadata(patrol_id))
            except (ReportNotFoundError, InvalidReportError):
                continue
        return reports

    def metadata(self, patrol_id: str) -> dict:
        report_dir = self._report_dir(patrol_id)
        path = report_dir / "metadata.json"
        if not path.is_file():
            raise ReportNotFoundError(f"metadata not found for patrol_id={patrol_id}")
        try:
            model = PatrolAggregate.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError) as exc:
            raise InvalidReportError(f"invalid metadata for patrol_id={patrol_id}") from exc
        return model.model_dump(mode="json")

    def markdown(self, patrol_id: str) -> str:
        path = self._report_dir(patrol_id) / "report.md"
        if not path.is_file():
            raise ReportNotFoundError(f"report.md not found for patrol_id={patrol_id}")
        return path.read_text(encoding="utf-8")

    def image(self, patrol_id: str, image_id: str) -> Path:
        if not _IMAGE_ID_PATTERN.fullmatch(image_id):
            raise InvalidReportError("invalid image_id")
        path = self._report_dir(patrol_id) / "images" / f"{image_id}.jpg"
        if not path.is_file():
            raise ReportNotFoundError(
                f"image not found for patrol_id={patrol_id}, image_id={image_id}"
            )
        return path

    def _report_dir(self, patrol_id: str) -> Path:
        if not PATROL_ID_PATTERN.fullmatch(patrol_id):
            raise InvalidReportError("patrol_id must match YYYYMMDD_HHMM")
        return self._report_root / patrol_id
