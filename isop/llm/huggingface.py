"""Optional cloud provider: Hugging Face Inference Providers (OpenAI-compatible chat completions router).

Comparison only — requires internet and HF_TOKEN; never used by the offline demo. JSON-schema
`response_format` support depends on the routed inference provider and model, so output is always
re-validated by the planner.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from isop.llm.base import LlmError


class HuggingFaceProvider:
    name = "huggingface"
    is_local = False

    def __init__(
        self,
        model: str,
        token: str | None,
        base_url: str = "https://router.huggingface.co/v1",
        timeout_s: float = 60.0,
        temperature: float = 0.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise LlmError("auth", "HF_TOKEN is not set")
        self.model = model
        self.temperature = temperature
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": 600,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "investigation_plan", "schema": schema, "strict": True}},
        }
        try:
            r = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise LlmError("timeout", "Hugging Face router timed out") from exc
        except httpx.TransportError as exc:
            raise LlmError("unavailable", "Hugging Face router not reachable") from exc
        if r.status_code in (401, 403):
            raise LlmError("auth", "Hugging Face rejected HF_TOKEN")
        if r.status_code == 404:
            raise LlmError("model_missing", f"model {self.model!r} not available")
        if r.status_code >= 400:
            raise LlmError("http_error", f"Hugging Face returned HTTP {r.status_code}")
        try:
            data = json.loads(r.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmError("malformed", "response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise LlmError("malformed", "response JSON is not an object")
        return data

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "reachable": None, "note": "cloud; not probed"}

    async def aclose(self) -> None:
        await self._http.aclose()
