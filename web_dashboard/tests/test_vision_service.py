import json
from email.message import Message
from unittest.mock import patch

import pytest

from web_dashboard.services.vision_service import (
    VisionCaptureService,
    VisionResponseError,
    VisionUnavailableError,
)


class FakeResponse:
    def __init__(self, body: dict | bytes, content_type: str = "application/json") -> None:
        self._body = json.dumps(body).encode() if isinstance(body, dict) else body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit=None):
        return self._body


def test_capture_uses_existing_transfer_and_images_endpoints():
    service = VisionCaptureService("http://vision.local:8000")
    responses = [
        FakeResponse({"requested": 1, "success": 1, "failed": 0}),
        FakeResponse(
            {"images": [{"filename": "crop.jpg", "url": "/media/2026/crop.jpg"}]}
        ),
    ]
    with patch(
        "web_dashboard.services.vision_service.urllib.request.urlopen",
        side_effect=responses,
    ) as urlopen:
        result = service.capture()

    assert urlopen.call_args_list[0].args[0].full_url.endswith("/control/request-transfer")
    assert urlopen.call_args_list[0].args[0].method == "POST"
    assert result["filename"] == "crop.jpg"
    assert result["image_url"] == "/api/camera/latest-image"
    assert result["transfer"]["success"] == 1


def test_latest_image_is_proxied_only_from_configured_origin():
    service = VisionCaptureService("http://vision.local:8000")
    responses = [
        FakeResponse({"images": [{"filename": "crop.jpg", "url": "/media/crop.jpg"}]}),
        FakeResponse(b"jpeg", "image/jpeg"),
    ]
    with patch(
        "web_dashboard.services.vision_service.urllib.request.urlopen",
        side_effect=responses,
    ):
        data, content_type = service.latest_image()

    assert data == b"jpeg"
    assert content_type == "image/jpeg"


def test_images_are_normalised_and_selected_image_is_proxied():
    service = VisionCaptureService("http://vision.local:8000")
    listing = {
        "images": [
            {
                "filename": "crop.jpg",
                "day": "2026-08-23",
                "rel_path": "2026-08-23/crop.jpg",
                "url": "/media/2026-08-23/crop.jpg",
            }
        ]
    }
    responses = [FakeResponse(listing), FakeResponse(listing), FakeResponse(b"jpeg", "image/jpeg")]
    with patch(
        "web_dashboard.services.vision_service.urllib.request.urlopen",
        side_effect=responses,
    ):
        images = service.images()
        data, content_type = service.image("2026-08-23/crop.jpg")

    assert images[0]["image_url"].startswith("/api/vision/image?path=")
    assert data == b"jpeg"
    assert content_type == "image/jpeg"


def test_delete_and_pi_cleanup_use_vis_contract():
    service = VisionCaptureService("http://vision.local:8000")
    with patch(
        "web_dashboard.services.vision_service.urllib.request.urlopen",
        side_effect=[
            FakeResponse({"deleted": ["2026-08-23/crop.jpg"], "rejected": []}),
            FakeResponse({"deleted": ["one.jpg", "two.jpg"], "pending_kept": 0}),
        ],
    ) as urlopen:
        deleted = service.delete_received(["2026-08-23/crop.jpg"])
        cleaned = service.delete_all_local()

    delete_request = urlopen.call_args_list[0].args[0]
    assert delete_request.full_url.endswith("/images/delete")
    assert json.loads(delete_request.data) == {"paths": ["2026-08-23/crop.jpg"]}
    assert urlopen.call_args_list[1].args[0].full_url.endswith("/control/delete-all-local")
    assert len(deleted["deleted"]) == 1
    assert len(cleaned["deleted"]) == 2


def test_capture_does_not_report_success_when_every_upload_failed():
    service = VisionCaptureService("http://vision.local:8000")
    with patch(
        "web_dashboard.services.vision_service.urllib.request.urlopen",
        return_value=FakeResponse({"requested": 2, "success": 0, "failed": 2}),
    ), pytest.raises(VisionResponseError, match="2장 실패"):
        service.capture()


def test_external_image_url_is_rejected():
    service = VisionCaptureService("http://vision.local:8000")
    response = FakeResponse(
        {"images": [{"filename": "crop.jpg", "url": "http://other.local/crop.jpg"}]}
    )
    with patch(
        "web_dashboard.services.vision_service.urllib.request.urlopen", return_value=response
    ), pytest.raises(VisionResponseError, match="벗어났습니다"):
        service.latest_image()


def test_unconfigured_vision_service_is_unavailable():
    with pytest.raises(VisionUnavailableError):
        VisionCaptureService(None).latest()
