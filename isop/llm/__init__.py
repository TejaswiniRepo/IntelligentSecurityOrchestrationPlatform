"""LLM provider layer. `build_llm_provider` is the only place that knows about concrete backends."""

from __future__ import annotations

from isop.config import Settings
from isop.llm.base import LlmError, LlmProvider
from isop.llm.ollama import OllamaProvider, is_loopback

__all__ = ["LlmError", "LlmProvider", "OllamaProvider", "build_llm_provider", "ConfigurationError"]


class ConfigurationError(ValueError):
    pass


def build_llm_provider(settings: Settings) -> LlmProvider | None:
    """Return the configured provider, or None for deterministic mode. Enforces offline mode."""
    p = settings.llm_provider
    if p == "deterministic":
        return None
    if p == "ollama":
        if settings.offline and not is_loopback(settings.ollama_base_url):
            raise ConfigurationError(
                f"ISOP_OFFLINE=true requires a loopback OLLAMA_BASE_URL, got {settings.ollama_base_url!r}"
            )
        return OllamaProvider(
            base_url=settings.ollama_base_url, model=settings.ollama_model, timeout_s=settings.llm_timeout_s,
            temperature=settings.llm_temperature, num_ctx=settings.ollama_num_ctx,
            keep_alive=settings.ollama_keep_alive,
        )
    if settings.offline:
        raise ConfigurationError(f"LLM_PROVIDER={p} needs internet; set ISOP_OFFLINE=false to use it")
    if p == "anthropic":
        from isop.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=settings.anthropic_model, timeout_s=settings.llm_timeout_s,
                                 effort=settings.anthropic_effort, api_key=settings.anthropic_api_key)
    if p == "huggingface":
        from isop.llm.huggingface import HuggingFaceProvider

        return HuggingFaceProvider(model=settings.hf_model, token=settings.hf_token, base_url=settings.hf_base_url,
                                   timeout_s=settings.llm_timeout_s, temperature=settings.llm_temperature)
    raise ConfigurationError(f"unknown LLM_PROVIDER {p!r}")
