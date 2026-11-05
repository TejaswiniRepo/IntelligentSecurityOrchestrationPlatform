"""Optional cloud provider: Claude via the official Anthropic SDK (Messages API, structured outputs).

Development/comparison only — never required by the offline demo. Credentials are resolved by the SDK
from the environment (ANTHROPIC_API_KEY, ...). A Claude Max / claude.ai login does NOT grant API access;
API usage is billed separately. Install with:  pip install -e ".[cloud]"
"""

from __future__ import annotations

import json
from typing import Any

from isop.llm.base import LlmError


class AnthropicProvider:
    name = "anthropic"
    is_local = False

    def __init__(self, model: str, timeout_s: float = 60.0, effort: str = "low", api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # optional dependency
            raise LlmError("unavailable", 'anthropic SDK not installed (pip install -e ".[cloud]")') from exc
        self._anthropic = anthropic
        self.model = model
        self.effort = effort
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / auth token / profile itself.
        kwargs = {"api_key": api_key} if api_key else {}
        self._client = anthropic.AsyncAnthropic(timeout=timeout_s, max_retries=1, **kwargs)

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        a = self._anthropic
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}, "effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as exc:
            raise LlmError("auth", "Anthropic API rejected the credentials") from exc
        except a.PermissionDeniedError as exc:
            raise LlmError("auth", "Anthropic API key lacks permission") from exc
        except a.NotFoundError as exc:
            raise LlmError("model_missing", f"model {self.model!r} not found") from exc
        except a.RateLimitError as exc:
            raise LlmError("http_error", "Anthropic API rate limit") from exc
        except a.APITimeoutError as exc:
            raise LlmError("timeout", "Anthropic API timed out") from exc
        except a.APIConnectionError as exc:
            raise LlmError("unavailable", "Anthropic API not reachable") from exc
        except a.APIStatusError as exc:
            raise LlmError("http_error", f"Anthropic API returned HTTP {exc.status_code}") from exc
        except a.AnthropicError as exc:  # e.g. no credentials configured
            raise LlmError("auth", "Anthropic client not configured (set ANTHROPIC_API_KEY)") from exc

        if response.stop_reason == "refusal":
            raise LlmError("refused", "model declined the request")
        if response.stop_reason == "max_tokens":
            raise LlmError("malformed", "response truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        try:
            data = json.loads(text or "")
        except ValueError as exc:
            raise LlmError("malformed", "response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise LlmError("malformed", "response JSON is not an object")
        return data

    async def health(self) -> dict[str, Any]:
        # No network call: a probe would spend API credits. Report configuration only.
        return {"provider": self.name, "model": self.model, "reachable": None, "note": "cloud; not probed"}

    async def aclose(self) -> None:
        await self._client.close()
