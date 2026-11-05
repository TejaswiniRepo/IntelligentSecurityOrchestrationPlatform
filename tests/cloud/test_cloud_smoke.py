"""Optional cloud-provider smoke tests. Skipped unless explicitly enabled; they cost money and need internet.

    $env:ISOP_RUN_CLOUD_TESTS="1"; $env:ANTHROPIC_API_KEY="..."; .venv\\Scripts\\python -m pytest tests/cloud -v
"""

from __future__ import annotations

import asyncio
import os

import pytest

from isop.planner import plan_schema

pytestmark = [
    pytest.mark.cloud,
    pytest.mark.skipif(os.getenv("ISOP_RUN_CLOUD_TESTS") != "1", reason="set ISOP_RUN_CLOUD_TESTS=1 to run"),
]

PROMPT = "Evidence: device seen before (score 55). Choose follow-up steps from: check_device_history."


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_anthropic_structured_plan() -> None:
    from isop.llm.anthropic_provider import AnthropicProvider

    async def run() -> dict:
        p = AnthropicProvider(model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
        try:
            return await p.complete_json("Return JSON only.", PROMPT, plan_schema())
        finally:
            await p.aclose()

    out = asyncio.run(run())
    assert isinstance(out["steps"], list)


@pytest.mark.skipif(not os.getenv("HF_TOKEN"), reason="HF_TOKEN not set")
def test_huggingface_structured_plan() -> None:
    from isop.llm.huggingface import HuggingFaceProvider

    async def run() -> dict:
        p = HuggingFaceProvider(model=os.getenv("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct"), token=os.environ["HF_TOKEN"])
        try:
            return await p.complete_json("Return JSON only.", PROMPT, plan_schema())
        finally:
            await p.aclose()

    out = asyncio.run(run())
    assert isinstance(out["steps"], list)
