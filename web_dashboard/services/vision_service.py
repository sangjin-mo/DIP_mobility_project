"""Dashboard adapter for the existing VIS image-transfer server.

The webcam Pi and VIS server remain owned by the vision team.  This adapter
uses only their published HTTP endpoints: request a pending-image transfer,
list/delete received images, clean uploaded Pi images, and proxy JPEGs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class VisionUnavailableError(ConnectionError):
    pass


class VisionResponseError(RuntimeError):
    pass


class VisionCaptureService:
    def __init__(
        self,
        server_url: str | None,
        *,
        timeout_s: float = 35.0,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._server_url = server_url.rstrip("/") if server_url else None
        self._timeout_s = timeout_s
        self._max_image_bytes = max_image_bytes

    @property
    def configured(self) -> bool:
        return bool(self._server_url)

    def capture(self) -> dict:
        """Ask VIS to upload pending frames, then return its newest image."""
        transfer = self._request_json("/control/request-transfer", method="POST")
        requested = transfer.get("requested")
        succeeded = transfer.get("success")
        failed = transfer.get("failed")
        if (
            isinstance(requested, int)
            and requested > 0
            and isinstance(succeeded, int)
            and succeeded == 0
            and isinstance(failed, int)
            and failed > 0
        ):
            raise VisionResponseError(f"웹캠 이미지 전송에 실패했습니다 ({failed}장 실패).")
        latest = self.latest()
        if latest is None:
            raise VisionResponseError("전송은 완료됐지만 비전 서버에 이미지가 없습니다.")
        latest["transfer"] = {
            "requested": requested,
            "success": succeeded,
            "failed": failed,
        }
        return latest

    def images(self) -> list[dict]:
        """Return every received VIS image using dashboard-owned proxy URLs."""
        payload = self._request_json("/images")
        images = payload.get("images")
        if not isinstance(images, list):
            raise VisionResponseError("비전 서버의 이미지 목록 형식이 올바르지 않습니다.")
        result = []
        for item in images:
            if not isinstance(item, dict):
                raise VisionResponseError("비전 서버의 이미지 정보가 올바르지 않습니다.")
            rel_path = item.get("rel_path")
            if not isinstance(rel_path, str) or not isinstance(item.get("url"), str):
                raise VisionResponseError("비전 서버의 이미지 경로가 올바르지 않습니다.")
            result.append(
                {
                    "filename": item.get("filename"),
                    "day": item.get("day"),
                    "rel_path": rel_path,
                    "image_url": "/api/vision/image?path=" + urllib.parse.quote(rel_path),
                }
            )
        return result

    def transfer(self) -> dict:
        """Request the VIS server to pull all pending Pi captures."""
        return self._request_json("/control/request-transfer", method="POST")

    def delete_received(self, paths: list[str]) -> dict:
        """Delete selected copies from the VIS PC received store."""
        return self._request_json("/images/delete", method="POST", payload={"paths": paths})

    def delete_all_local(self) -> dict:
        """Delete only upload-confirmed images from the webcam Raspberry Pi."""
        return self._request_json("/control/delete-all-local", method="POST")

    def image(self, rel_path: str) -> tuple[bytes, str]:
        """Proxy one selected image after resolving it from VIS's own list."""
        item = next(
            (
                item
                for item in self._raw_images()
                if isinstance(item, dict) and item.get("rel_path") == rel_path
            ),
            None,
        )
        if item is None or not isinstance(item.get("url"), str):
            raise VisionResponseError("비전 서버에서 선택한 이미지를 찾을 수 없습니다.")
        return self._read_image(item["url"])

    def latest(self) -> dict | None:
        item = self._latest_item()
        if item is None:
            return None
        return {
            "filename": item.get("filename"),
            "day": item.get("day"),
            "rel_path": item.get("rel_path"),
            "image_url": "/api/camera/latest-image",
        }

    def _latest_item(self) -> dict | None:
        images = self._raw_images()
        if not images:
            return None
        item = images[0]
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise VisionResponseError("비전 서버의 최신 이미지 정보가 올바르지 않습니다.")
        return item

    def latest_image(self) -> tuple[bytes, str]:
        item = self._latest_item()
        if item is None:
            raise VisionResponseError("비전 서버에 표시할 이미지가 없습니다.")
        return self._read_image(item["url"])

    def _raw_images(self) -> list[dict]:
        payload = self._request_json("/images")
        images = payload.get("images")
        if not isinstance(images, list):
            raise VisionResponseError("비전 서버의 이미지 목록 형식이 올바르지 않습니다.")
        return images

    def _read_image(self, url: str) -> tuple[bytes, str]:
        image_url = self._same_origin_url(url)
        request = urllib.request.Request(image_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                content_type = response.headers.get_content_type()
                data = response.read(self._max_image_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise VisionResponseError(
                f"비전 서버가 이미지 요청을 거부했습니다 (HTTP {exc.code})."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VisionUnavailableError(f"비전 서버 이미지에 연결할 수 없습니다: {exc}") from exc
        if len(data) > self._max_image_bytes:
            raise VisionResponseError("비전 이미지가 허용된 최대 크기를 초과했습니다.")
        if not content_type.startswith("image/"):
            raise VisionResponseError("비전 서버가 이미지가 아닌 응답을 반환했습니다.")
        return data, content_type

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
    ) -> dict:
        if not self._server_url:
            raise VisionUnavailableError(
                "비전 서버가 설정되지 않았습니다. DASHBOARD_VISION_SERVER_URL을 설정하세요."
            )
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = urllib.request.Request(
            f"{self._server_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VisionResponseError(
                f"비전 서버가 요청을 거부했습니다 (HTTP {exc.code}): {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VisionUnavailableError(f"비전 서버에 연결할 수 없습니다: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VisionResponseError("비전 서버가 올바른 JSON을 반환하지 않았습니다.") from exc
        if not isinstance(payload, dict):
            raise VisionResponseError("비전 서버 응답 형식이 올바르지 않습니다.")
        if payload.get("status") == "error":
            raise VisionResponseError(str(payload.get("reason", "비전 서버 요청 실패")))
        return payload

    def _same_origin_url(self, url: str) -> str:
        assert self._server_url is not None
        base = urllib.parse.urlparse(self._server_url)
        resolved = urllib.parse.urlparse(urllib.parse.urljoin(f"{self._server_url}/", url))
        if (resolved.scheme, resolved.netloc) != (base.scheme, base.netloc):
            raise VisionResponseError("비전 이미지 URL이 설정된 서버 주소를 벗어났습니다.")
        return resolved.geturl()
