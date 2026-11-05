"""Provider-neutral contract for LLM backends used by the investigation planner.

A provider only turns (system prompt, user prompt, JSON schema) into a JSON object. It knows nothing
about tools, MCP or policy, so the model can be swapped without touching security orchestration.
"""

from __future__ import annotations

from typing import Any, Protocol


class LlmError(Exception):
    """Any failure to obtain a usable answer. `kind` is safe to log and return in API metadata."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind  # unavailable | model_missing | timeout | auth | refused | malformed | http_error


class LlmProvider(Protocol):
    name: str  # "ollama" | "anthropic" | "huggingface"
    model: str
    is_local: bool  # True only if inference stays on this machine

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object conforming (as far as the backend can enforce) to `schema`."""
        ...

    async def health(self) -> dict[str, Any]:
        """Cheap readiness probe. Must not download anything."""
        ...

    async def aclose(self) -> None: ...
