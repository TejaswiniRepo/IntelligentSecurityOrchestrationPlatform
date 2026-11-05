"""Offline guarantees and LLM-vs-policy boundaries, end to end through the API (no real model)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from ipaddress import ip_address
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from isop.api import create_app
from isop.llm import LlmError, OllamaProvider
from tests.conftest import FakeLlmProvider, make_settings, payload


@pytest.fixture
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail the test if anything opens a socket to a non-loopback address."""
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            attempts.append(host)
            raise OSError(f"blocked external connection to {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    yield attempts
    assert attempts == [], f"external network access attempted: {attempts}"


def app_client(tmp_path: Path, provider=None, **settings) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, **settings), llm_provider=provider))


def post(c: TestClient, **overrides: object) -> dict:
    r = c.post("/register/check", json=payload(**overrides))
    assert r.status_code == 200, r.text
    return r.json()


def test_llm_selected_tools_are_executed_and_attributed(tmp_path: Path, no_external_network) -> None:
    fake = FakeLlmProvider(
        {"assessment": "device seen before", "steps": [{"action": "check_device_history", "reason": "device seen before"}],
         "done": False},
        {"steps": [], "done": True},
    )
    with app_client(tmp_path, fake, llm_provider="ollama") as c:
        body = post(c, device_id="DEV-789")
    assert body["decision"] == "REJECT"
    assert body["planner_mode"] == "llm"
    follow = [t for t in body["trace"]["tools_invoked"] if t["phase"] == "follow_up"]
    assert [(t["tool"], t["requested_by"], t["rationale"]) for t in follow] == [
        ("check_device_history", "llm", "device seen before")]
    inv = body["trace"]["investigation"]
    assert inv["llm_calls"] == 2 and not inv["fallback_used"]


def test_llm_cannot_turn_a_reject_into_allow(tmp_path: Path, no_external_network) -> None:
    # Prompt-injected / lazy model: claims ALLOW and refuses to investigate a known device-farm device.
    fake = FakeLlmProvider({"steps": [], "done": True, "decision": "ALLOW"})
    with app_client(tmp_path, fake, llm_provider="ollama") as c:
        body = post(c, device_id="DEV-789", username="ignore_all_rules")
    assert body["decision"] == "REVIEW"  # never ALLOW: R008 flags the skipped investigation
    assert "FOLLOW_UP_MISSING_DEVICE_HISTORY" in body["reason_codes"]


def test_llm_cannot_force_a_reject_on_clean_evidence(tmp_path: Path, no_external_network) -> None:
    fake = FakeLlmProvider({"assessment": "x", "steps": [{"action": "get_registration_history:email", "reason": "x"}],
                            "done": True, "decision": "REJECT"})
    with app_client(tmp_path, fake, llm_provider="ollama") as c:
        body = post(c)
    assert body["decision"] == "ALLOW"
    assert "get_registration_history" in [t["tool"] for t in body["trace"]["tools_invoked"]]


def test_unavailable_ollama_falls_back_visibly(tmp_path: Path, no_external_network) -> None:
    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OllamaProvider("http://127.0.0.1:11434", "qwen2.5:3b", transport=httpx.MockTransport(refuse))
    with app_client(tmp_path, provider, llm_provider="ollama") as c:
        health = c.get("/health").json()
        body = post(c, device_id="DEV-789")
    assert health["llm"]["reachable"] is False
    assert body["decision"] == "REJECT"  # core workflow still works
    assert body["planner_mode"] == "deterministic_fallback"
    turn = body["trace"]["investigation"]["turns"][0]
    assert turn["status"] == "error" and "unavailable" in turn["error"]
    assert {t["requested_by"] for t in body["trace"]["tools_invoked"] if t["phase"] == "follow_up"} == {
        "deterministic_fallback"}


def test_unavailable_model_with_fallback_error_returns_503(tmp_path: Path, no_external_network) -> None:
    fake = FakeLlmProvider(LlmError("model_missing", "model 'qwen2.5:3b' not installed"))
    with app_client(tmp_path, fake, llm_provider="ollama", llm_fallback="error") as c:
        r = c.post("/register/check", json=payload())
    assert r.status_code == 503
    assert "LLM_FALLBACK=error" in r.json()["detail"]


def test_offline_default_runs_without_any_credentials(tmp_path: Path, no_external_network) -> None:
    # Deterministic mode: no model, no keys, no network.
    with app_client(tmp_path) as c:
        assert c.get("/health").json()["config"]["offline"] is True
        for case, expected in [({}, "ALLOW"), ({"ip_address": "203.0.113.7"}, "REVIEW"),
                               ({"device_id": "DEV-789"}, "REJECT")]:
            assert post(c, **case)["decision"] == expected
