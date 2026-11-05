"""Local inference through the Ollama HTTP API (default provider for the offline demo).

Uses /api/chat with `format=<JSON schema>` (structured outputs: decoding is grammar-constrained to the
schema). Never calls /api/pull: a missing model is reported, not downloaded.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from isop.config import model_source
from isop.llm.base import LlmError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOOPBACK_HOSTS


class OllamaProvider:
    name = "ollama"
    is_local = True

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 60.0,
        temperature: float = 0.0,
        num_ctx: int = 4096,
        num_predict: int = 256,  # a plan is ~150 tokens; a low cap bounds slow CPU generations
        seed: int = 7,
        keep_alive: str = "30m",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.options = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict, "seed": seed}
        self.keep_alive = keep_alive
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s, transport=transport,
                                       trust_env=False)  # never route local inference through a proxy

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "options": self.options,
            "keep_alive": self.keep_alive,
        }
        try:
            # httpx timeouts apply per network read, not to the whole call; a slow CPU generation once ran
            # 137 s against a 60 s setting. wait_for makes timeout_s a hard deadline (Ollama stops generating
            # when the connection is dropped).
            r = await asyncio.wait_for(self._http.post("/api/chat", json=body), timeout=self.timeout_s)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LlmError("timeout", f"no response from Ollama within {self.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise LlmError("unavailable", f"Ollama not reachable at {self.base_url}") from exc

        if r.status_code == 404:
            raise LlmError("model_missing", f"model {self.model!r} not installed (run: ollama pull {self.model})")
        if r.status_code >= 400:
            raise LlmError("http_error", f"Ollama returned HTTP {r.status_code}")
        try:
            content = r.json()["message"]["content"]
            data = json.loads(content)
        except (KeyError, TypeError, ValueError) as exc:
            raise LlmError("malformed", "Ollama response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise LlmError("malformed", "Ollama returned JSON that is not an object")
        return data

    async def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            version = (await self._http.get("/api/version", timeout=3.0)).json().get("version")
            tags = (await self._http.get("/api/tags", timeout=3.0)).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return {"provider": self.name, "reachable": False, "model": self.model,
                    "model_source": model_source(self.model), "model_present": False}
        names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
        return {
            "provider": self.name,
            "reachable": True,
            "version": version,
            "model": self.model,
            "model_source": model_source(self.model),
            "model_present": self.model in names or f"{self.model}:latest" in names,
            "probe_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    async def aclose(self) -> None:
        await self._http.aclose()
