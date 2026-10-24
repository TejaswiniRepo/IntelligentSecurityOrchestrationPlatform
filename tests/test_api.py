"""End-to-end scenarios through FastAPI -> orchestrator -> MCP (in-process) -> policy -> audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASE_PAYLOAD, payload


def check(client: TestClient, **overrides: object) -> dict:
    r = client.post("/register/check", json=payload(**overrides))
    assert r.status_code == 200, r.text
    return r.json()


def tools_called(body: dict) -> list[str]:
    return [t["tool"] for t in body["trace"]["tools_invoked"]]


def rules(body: dict) -> set[str]:
    return {h["rule_id"] for h in body["trace"]["policy"]["rules_fired"]}


def test_normal_registration_is_allowed(client: TestClient) -> None:
    body = check(client)
    assert body["decision"] == "ALLOW"
    assert rules(body) == {"R000_NO_RISK_INDICATORS"}
    assert sorted(tools_called(body)) == sorted(
        ["check_email_reputation", "check_phone_risk", "check_ip_risk", "check_device_risk"]
    )
    assert body["simulated_signals"] is True
    assert all(s["simulated"] and s["source"].startswith("mock.") for s in body["trace"]["signals"])
    assert body["trace"]["tool_transport"] == "mcp/inprocess"


def test_phone_is_optional_and_not_checked_when_absent(client: TestClient) -> None:
    body = check(client, phone=None)
    assert body["decision"] == "ALLOW"
    assert "check_phone_risk" not in tools_called(body)


def test_tor_exit_ip_goes_to_review_with_corroborating_follow_up(client: TestClient) -> None:
    body = check(client, ip_address="203.0.113.7")
    assert body["decision"] == "REVIEW"
    assert "HIGH_RISK_IP" in body["reason_codes"]
    follow_ups = [t for t in body["trace"]["tools_invoked"] if t["phase"] == "follow_up"]
    assert [t["tool"] for t in follow_ups] == ["check_device_history"]
    assert "Conflicting evidence" in follow_ups[0]["rationale"]


def test_hosting_proxy_ip_goes_to_review(client: TestClient) -> None:
    body = check(client, ip_address="198.51.100.20")
    assert body["decision"] == "REVIEW"
    assert rules(body) == {"R005_ELEVATED_RISK_SIGNAL"}
    assert body["reason_codes"] == ["ELEVATED_RISK_IP"]


def test_disposable_email_alone_is_review_not_reject(client: TestClient) -> None:
    body = check(client, email="bob.smith@mailinator.com")
    assert body["decision"] == "REVIEW"
    assert "HIGH_RISK_EMAIL" in body["reason_codes"]


def test_device_linked_to_many_accounts_is_rejected_after_follow_up(client: TestClient) -> None:
    body = check(client, device_id="DEV-789")
    assert body["decision"] == "REJECT"
    assert "R002_HARD_REJECT_INDICATOR" in rules(body)
    assert "DEVICE_LINKED_TO_MANY_ACCOUNTS" in body["reason_codes"]
    history = next(s for s in body["trace"]["signals"] if s["signal_type"] == "DEVICE_HISTORY")
    assert history["details"]["linked_accounts"] == 17


def test_follow_up_can_clear_a_seen_before_device(client: TestClient) -> None:
    body = check(client, device_id="dev-shared-family")
    assert "check_device_history" in tools_called(body)
    assert body["decision"] == "ALLOW"


def test_emulator_plus_disposable_email_is_rejected(client: TestClient) -> None:
    body = check(client, device_id="emu-pixel-01", email="x@mailinator.com")
    assert body["decision"] == "REJECT"
    assert "R003_MULTIPLE_HIGH_RISK_SIGNALS" in rules(body)


def test_multiple_conflicting_signals_route_to_review(client: TestClient) -> None:
    # Clean email and device, but VoIP phone from another country (high) and a hosting IP (elevated).
    body = check(client, phone="+15555550123", country="GB", ip_address="198.51.100.20")
    assert body["decision"] == "REVIEW"
    assert {"R004_SINGLE_HIGH_RISK_SIGNAL", "R005_ELEVATED_RISK_SIGNAL"} <= rules(body)
    assert {"HIGH_RISK_PHONE", "ELEVATED_RISK_IP"} <= set(body["reason_codes"])
    assert "check_device_history" in tools_called(body)


def test_same_input_gives_same_decision(client: TestClient) -> None:
    a, b = check(client, device_id="DEV-789"), check(client, device_id="DEV-789")
    assert (a["decision"], a["reason_codes"], a["composite_score"]) == (
        b["decision"], b["reason_codes"], b["composite_score"]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": None},
        {"ip_address": None},
        {"device_id": None},
        {"email": "not-an-email"},
        {"phone": "4155550123"},
        {"ip_address": "999.1.1.1"},
        {"country": "USA"},
        {"device_id": "bad id with spaces"},
        {"username": "<script>"},
        {"password": "short"},
        {"is_admin": True},
    ],
)
def test_missing_or_invalid_fields_are_rejected_with_422(client: TestClient, overrides: dict) -> None:
    r = client.post("/register/check", json=payload(**overrides))
    assert r.status_code == 422
    assert all(set(e) == {"loc", "msg", "type"} for e in r.json()["detail"])  # no echoed "input"


def test_validation_error_never_echoes_password(client: TestClient) -> None:
    r = client.post("/register/check", json=payload(password="tiny-secret", email="broken"))
    assert r.status_code == 422
    assert "tiny-secret" not in r.text


def test_audit_trace_is_retrievable_and_free_of_sensitive_data(client: TestClient, audit_path: Path) -> None:
    body = check(client, device_id="DEV-789")
    r = client.get(f"/audit/{body['correlation_id']}")
    assert r.status_code == 200
    assert r.json()["decision"] == "REJECT"

    written = audit_path.read_text(encoding="utf-8")
    assert body["correlation_id"] in written
    for raw in (BASE_PAYLOAD["password"], BASE_PAYLOAD["email"], BASE_PAYLOAD["phone"], "192.0.2.10", "DEV-789"):
        assert raw not in written, f"raw value leaked into audit log: {raw}"
    assert "a***@gmail.com" in written and "192.0.2.0/24" in written


def test_unknown_audit_id_is_404(client: TestClient) -> None:
    assert client.get("/audit/does-not-exist").status_code == 404
