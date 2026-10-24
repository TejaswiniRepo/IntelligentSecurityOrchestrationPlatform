"""Runtime settings from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    planner: str = "deterministic"  # only "deterministic" is implemented; see README for LLM
    tool_transport: str = "stdio"  # "stdio" (MCP subprocess) | "inprocess" (MCP in-memory, tests)
    tool_timeout_s: float = 3.0
    max_follow_ups: int = 3
    policy_path: Path = ROOT / "config" / "policy.json"
    audit_path: Path = ROOT / "var" / "audit.jsonl"

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            planner=os.getenv("ISOP_PLANNER", d.planner),
            tool_transport=os.getenv("ISOP_TOOL_TRANSPORT", d.tool_transport),
            tool_timeout_s=float(os.getenv("ISOP_TOOL_TIMEOUT_S", d.tool_timeout_s)),
            max_follow_ups=int(os.getenv("ISOP_MAX_FOLLOW_UPS", d.max_follow_ups)),
            policy_path=Path(os.getenv("ISOP_POLICY_PATH", d.policy_path)),
            audit_path=Path(os.getenv("ISOP_AUDIT_PATH", d.audit_path)),
        )
