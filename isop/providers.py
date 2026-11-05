"""Deterministic mock signal providers.

Every result is labelled `simulated=True` and `source="mock.*"`. Scores are pure functions of the
input so the same request always yields the same evidence. Network ranges used for "bad" IPs are
the RFC 5737 documentation ranges, so no real address is ever labelled malicious.

To integrate a real provider, implement a function with the same signature that returns a
`ProviderResult` with `simulated=False` and its real `source`, and register it in `mcp_server.py`.
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network

from isop.schemas import ProviderResult, SignalType

# ----------------------------------------------------------------------- email

DISPOSABLE_DOMAINS = frozenset(
    {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.dev", "yopmail.com", "trashmail.com"}
)
ESTABLISHED_PROVIDERS = frozenset({"gmail.com", "outlook.com", "yahoo.com", "proton.me", "icloud.com"})


def email_reputation(email: str) -> ProviderResult:
    local, _, domain = email.lower().rpartition("@")
    reasons: list[str] = []
    if domain in DISPOSABLE_DOMAINS:
        score, reasons = 90, ["DISPOSABLE_EMAIL_DOMAIN"]
    elif domain in ESTABLISHED_PROVIDERS:
        score, reasons = 10, ["ESTABLISHED_EMAIL_PROVIDER"]
    else:
        score, reasons = 30, ["UNKNOWN_EMAIL_DOMAIN"]

    digits = sum(c.isdigit() for c in local)
    if digits >= 6:
        score = min(100, score + 25)
        reasons.append("NUMERIC_HEAVY_LOCAL_PART")

    return ProviderResult(
        signal_type=SignalType.EMAIL,
        risk_score=score,
        reason_codes=reasons,
        source="mock.email_reputation",
        simulated=True,
        details={"domain_category": reasons[0].lower()},
    )


# ----------------------------------------------------------------------- phone

CALLING_CODES = {"+1": "US", "+44": "GB", "+91": "IN", "+49": "DE", "+61": "AU"}
VOIP_PREFIXES = ("+1555", "+4470")  # fictional / personal-number ranges
PREMIUM_PREFIXES = ("+1900",)


def phone_risk(phone: str, country: str | None) -> ProviderResult:
    reasons: list[str] = []
    if phone.startswith(PREMIUM_PREFIXES):
        score, line_type, reasons = 90, "premium", ["PREMIUM_RATE_NUMBER"]
    elif phone.startswith(VOIP_PREFIXES):
        score, line_type, reasons = 65, "voip", ["VOIP_NUMBER"]
    else:
        score, line_type, reasons = 10, "mobile", ["MOBILE_NUMBER"]

    phone_country = next((cc for code, cc in sorted(CALLING_CODES.items(), key=lambda kv: -len(kv[0]))
                          if phone.startswith(code)), "UNKNOWN")
    if not country:
        reasons.append("COUNTRY_NOT_SUPPLIED")  # mismatch rule cannot be evaluated
    elif phone_country != country.upper():
        score = min(100, score + 20)
        reasons.append("PHONE_COUNTRY_MISMATCH")

    return ProviderResult(
        signal_type=SignalType.PHONE,
        risk_score=score,
        reason_codes=reasons,
        source="mock.phone_risk",
        simulated=True,
        details={"line_type": line_type, "phone_country": phone_country},
    )


# ----------------------------------------------------------------------- ip

TOR_EXIT_RANGE = ip_network("203.0.113.0/24")  # TEST-NET-3
HOSTING_RANGE = ip_network("198.51.100.0/24")  # TEST-NET-2
RESIDENTIAL_RANGE = ip_network("192.0.2.0/24")  # TEST-NET-1


def ip_risk(ip: str) -> ProviderResult:
    addr = ip_address(ip)
    if addr in TOR_EXIT_RANGE:
        score, network, reasons = 95, "tor_exit", ["TOR_EXIT_NODE"]
    elif addr in HOSTING_RANGE:
        score, network, reasons = 65, "hosting", ["HOSTING_PROVIDER", "ANONYMIZING_PROXY"]
    elif addr in RESIDENTIAL_RANGE:
        score, network, reasons = 15, "residential", ["RESIDENTIAL_NETWORK"]
    elif addr.is_private or addr.is_loopback:
        score, network, reasons = 10, "private", ["PRIVATE_NETWORK"]
    else:
        score, network, reasons = 25, "unknown", ["UNCLASSIFIED_NETWORK"]

    return ProviderResult(
        signal_type=SignalType.IP,
        risk_score=score,
        reason_codes=reasons,
        source="mock.ip_risk",
        simulated=True,
        details={"network_type": network},
    )


# ----------------------------------------------------------------------- device

# Synthetic device registry: device_id -> (linked accounts, prior rejected registrations)
DEVICE_REGISTRY: dict[str, tuple[int, int]] = {
    "DEV-789": (17, 6),
    "dev-shared-family": (3, 0),
}


def device_risk(device_id: str) -> ProviderResult:
    if device_id.lower().startswith("emu-"):
        score, reasons = 85, ["EMULATOR_DETECTED"]
    elif device_id in DEVICE_REGISTRY:
        score, reasons = 55, ["DEVICE_SEEN_BEFORE"]
    else:
        score, reasons = 15, ["NEW_DEVICE"]

    return ProviderResult(
        signal_type=SignalType.DEVICE,
        risk_score=score,
        reason_codes=reasons,
        source="mock.device_risk",
        simulated=True,
        details={"emulator": reasons[0] == "EMULATOR_DETECTED"},
    )


def device_history(device_id: str) -> ProviderResult:
    linked, rejected = DEVICE_REGISTRY.get(device_id, (0, 0))
    if linked >= 10:
        score, reasons = 92, ["DEVICE_LINKED_TO_MANY_ACCOUNTS"]
    elif linked >= 3:
        score, reasons = 40, ["DEVICE_SHARED_BY_FEW_ACCOUNTS"]
    else:
        score, reasons = 5, ["NO_PRIOR_DEVICE_HISTORY"]
    if rejected > 0:
        score = min(100, score + 5)
        reasons.append("PRIOR_REJECTED_REGISTRATIONS")

    return ProviderResult(
        signal_type=SignalType.DEVICE_HISTORY,
        risk_score=score,
        reason_codes=reasons,
        source="mock.device_history",
        simulated=True,
        details={"linked_accounts": linked, "prior_rejections": rejected, "window_days": 90},
    )


# ----------------------------------------------------------------------- registration history

# Synthetic history service: registrations seen in the last 24 h per IP /24 network, phone or email.
IP_NETWORK_REGISTRATIONS: dict[str, int] = {"203.0.113.0/24": 2, "198.51.100.0/24": 9}
PHONE_REGISTRATIONS: dict[str, int] = {"+15555550123": 2, "+15555550100": 7}
EMAIL_REGISTRATIONS: dict[str, int] = {}


def registration_history(entity_type: str, entity_value: str) -> ProviderResult:
    if entity_type == "ip":
        count = IP_NETWORK_REGISTRATIONS.get(str(ip_network(f"{entity_value}/24", strict=False)), 0)             if ip_address(entity_value).version == 4 else 0
    elif entity_type == "phone":
        count = PHONE_REGISTRATIONS.get(entity_value, 0)
    elif entity_type == "email":
        count = EMAIL_REGISTRATIONS.get(entity_value.lower(), 0)
    else:
        raise ValueError("unsupported entity_type")

    if count >= 15:
        score, reasons = 90, ["HIGH_REGISTRATION_VELOCITY"]
    elif count >= 5:
        score, reasons = 70, ["ELEVATED_REGISTRATION_VELOCITY"]
    elif count >= 2:
        score, reasons = 30, ["SOME_PRIOR_REGISTRATIONS"]
    else:
        score, reasons = 5, ["NO_PRIOR_REGISTRATIONS"]

    return ProviderResult(
        signal_type=SignalType.REGISTRATION_HISTORY,
        risk_score=score,
        reason_codes=reasons,
        source="mock.registration_history",
        simulated=True,
        details={"entity_type": entity_type, "registrations_24h": count, "window_hours": 24},
    )
