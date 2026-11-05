"""Shared fixtures. The default suite is offline: no Ollama, no cloud, no credentials, no network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base = dict(llm_provider="deterministic", tool_transport="inprocess", audit_db_path=tmp_path / "audit.db")
    return Settings(**(base | overrides))


class FakeLlmProvider:
    """Scripted stand-in for an LLM backend. Each call pops the next response (dict or exception)."""

    name = "ollama"
    model = "fake-model"
    is_local = True

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append((system, user))
        if not self.responses:
            return {"steps": [], "done": True}
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return json.loads(json.dumps(r))

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "reachable": True, "model": self.model, "model_present": True}

    async def aclose(self) -> None:
        pass


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c
