"""ISOP signal MCP server.

Exposes narrowly scoped, read-only risk-signal tools. The server is its own trust boundary, so it
re-validates every argument instead of trusting the caller. Run standalone over stdio with:

    python -m isop.mcp_server
"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError
from pydantic import EmailStr

from isop import providers
from isop.schemas import DEVICE_ID, E164

server = MCPServer(
    name="isop-signals",
    instructions="Read-only registration risk signals. All results are simulated unless simulated=false.",
)

_email = TypeAdapter(EmailStr)


def _require(ok: bool, field: str) -> None:
    # Never echo the offending value: it may be personal data.
    if not ok:
        raise ToolError(f"invalid argument: {field}")


def _valid_email(v: str) -> bool:
    try:
        _email.validate_python(v)
        return True
    except ValidationError:
        return False


def _valid_ip(v: str) -> bool:
    try:
        ip_address(v)
        return True
    except ValueError:
        return False


@server.tool(description="Email reputation: disposable domains, provider category, local-part heuristics.")
def check_email_reputation(email: str) -> dict[str, Any]:
    _require(_valid_email(email), "email")
    return providers.email_reputation(email).model_dump(mode="json")


@server.tool(description="Phone risk: line type (mobile/VoIP/premium) and country consistency.")
def check_phone_risk(phone: str, country: str) -> dict[str, Any]:
    _require(bool(E164.fullmatch(phone)), "phone")
    _require(country == "" or (len(country) == 2 and country.isalpha()), "country")  # "" = not supplied
    return providers.phone_risk(phone, country or None).model_dump(mode="json")


@server.tool(description="IP risk: Tor exit, hosting/proxy, residential classification.")
def check_ip_risk(ip_address: str) -> dict[str, Any]:
    _require(_valid_ip(ip_address), "ip_address")
    return providers.ip_risk(ip_address).model_dump(mode="json")


@server.tool(description="Device risk: emulator detection and whether the device was seen before.")
def check_device_risk(device_id: str) -> dict[str, Any]:
    _require(bool(DEVICE_ID.fullmatch(device_id)), "device_id")
    return providers.device_risk(device_id).model_dump(mode="json")


@server.tool(description="Follow-up: accounts and prior outcomes linked to a device in the last 90 days.")
def check_device_history(device_id: str) -> dict[str, Any]:
    _require(bool(DEVICE_ID.fullmatch(device_id)), "device_id")
    return providers.device_history(device_id).model_dump(mode="json")


@server.tool(description="Follow-up: registrations in the last 24 h sharing this IP network, phone or email.")
def get_registration_history(entity_type: str, entity_value: str) -> dict[str, Any]:
    validators = {
        "ip": _valid_ip,
        "phone": lambda v: bool(E164.fullmatch(v)),
        "email": _valid_email,
    }
    _require(entity_type in validators, "entity_type")
    _require(validators[entity_type](entity_value), "entity_value")
    return providers.registration_history(entity_type, entity_value).model_dump(mode="json")


if __name__ == "__main__":
    server.run("stdio")
