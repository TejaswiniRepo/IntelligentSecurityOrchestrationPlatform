"""Deterministic demo scenarios. Every signal they produce comes from the simulated providers.

`expected` is the outcome under the deterministic rules planner. With the local LLM planner the policy
still decides; if the model skips a required follow-up, rule R008 makes the outcome REVIEW (never ALLOW).
"""

from __future__ import annotations

from typing import Any

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "normal",
        "title": "Normal registration",
        "expected": "ALLOW",
        "summary": "Established email provider, residential IP, new device, matching mobile number.",
        "payload": {"first_name": "Alice", "last_name": "Walker", "email": "alice.walker@gmail.com",
                    "phone": "+14155550123", "country": "US", "ip_address": "192.0.2.10",
                    "device_id": "dev-a1b2c3", "device_fingerprint": "fp-3c9a71d2e8b4"},
    },
    {
        "id": "suspicious",
        "title": "Suspicious registration",
        "expected": "REVIEW",
        "summary": "Clean email and device, but the IP is a simulated Tor exit node: conflicting evidence.",
        "payload": {"first_name": "Bob", "last_name": "Turner", "email": "bob.t@gmail.com",
                    "phone": "+14155550188", "country": "US", "ip_address": "203.0.113.7",
                    "device_id": "dev-x9y8z7"},
    },
    {
        "id": "high-risk",
        "title": "High-risk registration",
        "expected": "REJECT",
        "summary": "Hosting-provider IP and a device already linked to 17 accounts (simulated device farm).",
        "payload": {"first_name": "Carol", "last_name": "Mendes", "email": "carol88@outlook.com",
                    "phone": "+14155550199", "country": "US", "ip_address": "198.51.100.20",
                    "device_id": "DEV-789"},
    },
    {
        "id": "conflicting",
        "title": "Conflicting signals",
        "expected": "REVIEW",
        "summary": "VoIP number from another country and a hosting IP, with a clean email and new device.",
        "payload": {"first_name": "Dana", "last_name": "Kumar", "email": "dana.k@outlook.com",
                    "phone": "+15555550123", "country": "GB", "ip_address": "198.51.100.20",
                    "device_id": "dev-q1w2e3"},
    },
]
