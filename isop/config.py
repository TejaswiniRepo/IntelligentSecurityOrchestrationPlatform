"""Runtime settings from environment variables and an optional project-root `.env` (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LLM_PROVIDERS = ("ollama", "anthropic", "huggingface", "deterministic")


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser. Real environment variables always win over the file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Investigation planner / LLM
    llm_provider: str = "ollama"
    llm_fallback: str = "deterministic"  # "deterministic" | "error"
    llm_timeout_s: float = 45.0  # hard deadline per LLM call (cold model load measured at ~26 s)
    llm_temperature: float = 0.0
    llm_max_iterations: int = 3
    llm_cooldown_s: float = 30.0  # after an LLM failure, skip straight to fallback for this long
    offline: bool = True  # refuse cloud providers and non-loopback model endpoints

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_num_ctx: int = 4096
    ollama_keep_alive: str = "30m"

    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"
    anthropic_api_key: str | None = field(default=None, repr=False)  # if unset, the SDK resolves credentials from the environment

    hf_model: str = "Qwen/Qwen2.5-7B-Instruct"
    hf_base_url: str = "https://router.huggingface.co/v1"
    hf_token: str | None = field(default=None, repr=False)

    # Tools / policy / audit
    tool_transport: str = "stdio"  # "stdio" (MCP subprocess) | "inprocess" (MCP in-memory, tests)
    tool_timeout_s: float = 3.0
    max_follow_ups: int = 4
    policy_path: Path = ROOT / "config" / "policy.json"
    audit_db_path: Path = ROOT / "var" / "audit.db"

    def __post_init__(self) -> None:
        if self.llm_provider not in LLM_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {LLM_PROVIDERS}, got {self.llm_provider!r}")
        if self.llm_fallback not in ("deterministic", "error"):
            raise ValueError("LLM_FALLBACK must be 'deterministic' or 'error'")

    @classmethod
    def from_env(cls, env_file: Path | None = ROOT / ".env") -> Settings:
        env = {**(load_dotenv(env_file) if env_file else {}), **os.environ}
        d = cls()

        def get(key: str, default: object) -> str:
            return env.get(key, str(default))

        return cls(
            llm_provider=get("LLM_PROVIDER", d.llm_provider).lower(),
            llm_fallback=get("LLM_FALLBACK", d.llm_fallback).lower(),
            llm_timeout_s=float(get("LLM_TIMEOUT_S", d.llm_timeout_s)),
            llm_temperature=float(get("LLM_TEMPERATURE", d.llm_temperature)),
            llm_max_iterations=int(get("LLM_MAX_ITERATIONS", d.llm_max_iterations)),
            llm_cooldown_s=float(get("LLM_COOLDOWN_S", d.llm_cooldown_s)),
            offline=_bool(get("ISOP_OFFLINE", d.offline)),
            ollama_base_url=get("OLLAMA_BASE_URL", d.ollama_base_url),
            ollama_model=get("OLLAMA_MODEL", d.ollama_model),
            ollama_num_ctx=int(get("OLLAMA_NUM_CTX", d.ollama_num_ctx)),
            ollama_keep_alive=get("OLLAMA_KEEP_ALIVE", d.ollama_keep_alive),
            anthropic_model=get("ANTHROPIC_MODEL", d.anthropic_model),
            anthropic_effort=get("ANTHROPIC_EFFORT", d.anthropic_effort),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            hf_model=get("HF_MODEL", d.hf_model),
            hf_base_url=get("HF_BASE_URL", d.hf_base_url),
            hf_token=env.get("HF_TOKEN") or None,
            tool_transport=get("ISOP_TOOL_TRANSPORT", d.tool_transport),
            tool_timeout_s=float(get("ISOP_TOOL_TIMEOUT_S", d.tool_timeout_s)),
            max_follow_ups=int(get("ISOP_MAX_FOLLOW_UPS", d.max_follow_ups)),
            policy_path=Path(get("ISOP_POLICY_PATH", d.policy_path)),
            audit_db_path=Path(get("ISOP_AUDIT_DB", d.audit_db_path)),
        )

    def public_summary(self) -> dict[str, object]:
        """Configuration safe to expose on /health (no secrets)."""
        model = {"ollama": self.ollama_model, "anthropic": self.anthropic_model,
                 "huggingface": self.hf_model}.get(self.llm_provider)
        return {"llm_provider": self.llm_provider, "model": model, "llm_fallback": self.llm_fallback,
                "offline": self.offline, "tool_transport": self.tool_transport}
