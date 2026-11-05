"""Real local-LLM smoke test. Runs only when Ollama is up on 127.0.0.1 and the configured model is pulled.

    $env:OLLAMA_MODEL="qwen2.5:3b"; .venv\\Scripts\\python -m pytest tests/integration/test_ollama_smoke.py -v -s
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isop.api import create_app
from isop.llm import OllamaProvider
from isop.planner import plan_schema
from tests.conftest import make_settings, payload

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


def _ready() -> bool:
    async def probe() -> bool:
        p = OllamaProvider(BASE_URL, MODEL)
        try:
            h = await p.health()
            return bool(h.get("reachable") and h.get("model_present"))
        finally:
            await p.aclose()

    return asyncio.run(probe())


pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(not _ready(), reason=f"Ollama not running at {BASE_URL} or model {MODEL} not pulled"),
]


def test_real_structured_inference() -> None:
    async def run() -> dict:
        p = OllamaProvider(BASE_URL, MODEL, timeout_s=180)
        try:
            return await p.complete_json(
                "Return JSON only.",
                "EVIDENCE:\n- DEVICE: score=55 status=SUCCESS codes=DEVICE_SEEN_BEFORE\n"
                "AVAILABLE ACTIONS: check_device_history",
                plan_schema(),
            )
        finally:
            await p.aclose()

    out = asyncio.run(run())
    assert isinstance(out.get("steps"), list) and isinstance(out.get("done"), bool)


def test_end_to_end_with_real_model(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, llm_provider="ollama", ollama_model=MODEL, llm_timeout_s=180)
    with TestClient(create_app(settings)) as c:
        body = c.post("/register/check", json=payload(device_id="DEV-789")).json()
    turns = body["trace"]["investigation"]["turns"]
    print("\nplanner turns:", [(t["mode"], t["status"], t["latency_ms"], [s["tool"] for s in t["proposed"]])
                               for t in turns])
    assert turns[0]["mode"] == "llm" and turns[0]["status"] == "ok"  # genuine inference happened
    assert body["decision"] in ("REJECT", "REVIEW")  # never ALLOW for a device-farm device
