"""MCP client wrapper that turns tool calls into normalized `Signal`s.

Tool output is untrusted: it is schema-validated and its signal type must match the tool. Errors,
timeouts and malformed results become non-SUCCESS signals, never low-risk ones.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any, Protocol

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import ValidationError

from isop.config import ROOT
from isop.schemas import ProviderResult, Signal, SignalStatus, SignalType

log = logging.getLogger("isop.tools")

TOOL_SIGNAL_TYPE: dict[str, SignalType] = {
    "check_email_reputation": SignalType.EMAIL,
    "check_phone_risk": SignalType.PHONE,
    "check_ip_risk": SignalType.IP,
    "check_device_risk": SignalType.DEVICE,
    "check_device_history": SignalType.DEVICE_HISTORY,
    "get_registration_history": SignalType.REGISTRATION_HISTORY,
}


class ToolClient(Protocol):
    transport: str

    async def call(self, tool: str, arguments: dict[str, str]) -> Signal: ...


class McpToolClient:
    """Long-lived MCP session to the isop-signals server (stdio subprocess or in-process)."""

    def __init__(self, transport: str, timeout_s: float, inprocess_server: Any = None) -> None:
        if transport not in ("stdio", "inprocess"):
            raise ValueError(f"Unsupported ISOP_TOOL_TRANSPORT={transport!r}")
        self.transport = transport
        self.timeout_s = timeout_s
        self._inprocess_server = inprocess_server
        self._client: Client | None = None
        self.available_tools: frozenset[str] = frozenset()

    async def __aenter__(self) -> McpToolClient:
        if self.transport == "stdio":
            target = StdioServerParameters(command=sys.executable, args=["-m", "isop.mcp_server"], cwd=str(ROOT))
        else:
            from isop.mcp_server import server  # in-memory MCP, used by tests
            target = self._inprocess_server or server
        self._client = Client(target)
        await self._client.__aenter__()
        self.available_tools = frozenset(t.name for t in (await self._client.list_tools()).tools)
        missing = set(TOOL_SIGNAL_TYPE) - self.available_tools
        if missing:
            # Keep serving: calls to these tools fail closed (ERROR -> REVIEW) instead of blocking the demo.
            log.error("mcp server is missing tools=%s; affected checks will fail closed", sorted(missing))
        log.info("mcp connected transport=%s tools=%s", self.transport, sorted(self.available_tools))
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def call(self, tool: str, arguments: dict[str, str]) -> Signal:
        if self._client is None:
            raise RuntimeError("McpToolClient used outside its context")
        expected = TOOL_SIGNAL_TYPE[tool]
        start = time.perf_counter()

        def failed(status: SignalStatus, error: str) -> Signal:
            ms = round((time.perf_counter() - start) * 1000, 1)
            log.warning("tool=%s status=%s error=%s latency_ms=%s", tool, status, error, ms)
            return Signal(signal_type=expected, tool=tool, status=status, source="unavailable",
                          simulated=False, latency_ms=ms, error=error)

        if tool not in self.available_tools:
            return failed(SignalStatus.ERROR, "tool not available on MCP server")
        try:
            result = await asyncio.wait_for(self._client.call_tool(tool, arguments), timeout=self.timeout_s)
        except TimeoutError:
            return failed(SignalStatus.TIMEOUT, f"no response within {self.timeout_s}s")
        except Exception as exc:  # transport failure: report, never crash the investigation
            return failed(SignalStatus.ERROR, f"transport error: {type(exc).__name__}")

        if result.is_error:
            text = next((getattr(b, "text", "") for b in result.content), "")
            return failed(SignalStatus.ERROR, text[:200] or "tool reported an error")
        try:
            pr = ProviderResult.model_validate(result.structured_content)
        except ValidationError as exc:
            return failed(SignalStatus.ERROR, f"malformed tool output ({exc.error_count()} errors)")
        if pr.signal_type is not expected:
            return failed(SignalStatus.ERROR, f"tool returned {pr.signal_type}, expected {expected}")

        ms = round((time.perf_counter() - start) * 1000, 1)
        log.info("tool=%s status=SUCCESS score=%s latency_ms=%s", tool, pr.risk_score, ms)
        return Signal(
            signal_type=pr.signal_type, tool=tool, status=SignalStatus.SUCCESS, risk_score=pr.risk_score,
            reason_codes=pr.reason_codes, source=pr.source, simulated=pr.simulated, details=pr.details,
            latency_ms=ms,
        )
