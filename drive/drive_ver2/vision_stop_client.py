"""Authenticated STOP command client for the separate Vision Raspberry Pi."""

from __future__ import annotations

import json
import time
import urllib.request
import uuid


def send_stop_sign_stop(control_url: str, token: str, timeout_s: float = 2.0) -> dict:
    """Latch the drive Pi in STOPPED state after a stop sign is confirmed."""
    payload = {
        "command": "STOP",
        "command_id": str(uuid.uuid4()),
        "sent_at_ms": int(time.time() * 1000),
        "source": "VISION",
        "reason": "STOP_SIGN",
    }
    request = urllib.request.Request(
        control_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))
