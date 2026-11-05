"""Integration test over the real MCP stdio transport (spawns `python -m isop.mcp_server`)."""

from __future__ import annotations

import pytest

from isop.schemas import SignalStatus, SignalType
from isop.tool_client import McpToolClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_stdio_server_lists_tools_and_returns_validated_signals() -> None:
    async with McpToolClient("stdio", timeout_s=10.0) as tools:
        ok = await tools.call("check_ip_risk", {"ip_address": "203.0.113.9"})
        assert ok.status is SignalStatus.SUCCESS
        assert (ok.signal_type, ok.risk_score, ok.simulated) == (SignalType.IP, 95, True)

        bad = await tools.call("check_email_reputation", {"email": "secret-value-not-an-email"})
        assert bad.status is SignalStatus.ERROR
        assert "secret-value" not in (bad.error or "")  # server never echoes rejected input
