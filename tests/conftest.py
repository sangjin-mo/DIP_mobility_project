from __future__ import annotations

import socket
from pathlib import Path

import pytest

from ai_report.ingest.store import Store


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "sessions.db")
    yield s
    s.close()
