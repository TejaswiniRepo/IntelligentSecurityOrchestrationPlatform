from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isop.api import create_app
from isop.config import Settings

BASE_PAYLOAD = {
    "username": "alice_w",
    "email": "alice.walker@gmail.com",
    "password": "correct-horse-battery-staple",
    "phone": "+14155550123",
    "country": "US",
    "ip_address": "192.0.2.10",
    "device_id": "dev-a1b2c3",
}


def payload(**overrides: object) -> dict[str, object]:
    body = dict(BASE_PAYLOAD)
    for k, v in overrides.items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    return body


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def client(audit_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(tool_transport="inprocess", audit_path=audit_path))
    with TestClient(app) as c:
        yield c
