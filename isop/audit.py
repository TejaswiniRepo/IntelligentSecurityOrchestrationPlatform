"""Audit trail in SQLite (stdlib). One row per investigation; the full trace is stored as JSON.

Only masked identifiers are recorded. Passwords never reach this module (see InvestigationSubject).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from isop.schemas import AuditTrace

log = logging.getLogger("isop.audit")


def mask_email(email: str) -> str:
    local, _, domain = email.rpartition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}******{phone[-2:]}"


def mask_ip(ip: str) -> str:
    addr = ip_address(ip)
    prefix = 24 if addr.version == 4 else 48
    return str(ip_network(f"{ip}/{prefix}", strict=False))


def pseudonymize(value: str) -> str:
    # Stable pseudonym so investigators can correlate repeats without storing the raw identifier.
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:12]


MASKERS = {"email": mask_email, "phone": mask_phone, "ip_address": mask_ip, "device_id": pseudonymize}
ENTITY_MASKERS = {"email": mask_email, "phone": mask_phone, "ip": mask_ip}


def mask_arguments(arguments: dict[str, str]) -> dict[str, str]:
    masked = {}
    for k, v in arguments.items():
        if k == "entity_value":
            masked[k] = ENTITY_MASKERS.get(arguments.get("entity_type", ""), pseudonymize)(v)
        else:
            masked[k] = MASKERS.get(k, lambda x: x)(v)
    return masked


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_trace (
    correlation_id  TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    decision        TEXT NOT NULL,
    planner_mode    TEXT NOT NULL,
    llm_provider    TEXT NOT NULL,
    llm_model       TEXT,
    composite_score REAL NOT NULL,
    policy_version  TEXT NOT NULL,
    rules_fired     TEXT NOT NULL,
    tools_invoked   TEXT NOT NULL,
    duration_ms     REAL NOT NULL,
    trace_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_trace(created_at);
"""


class AuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def record(self, trace: AuditTrace) -> None:
        inv = trace.investigation
        row = (
            trace.correlation_id, trace.timestamp, trace.decision.value, inv.effective_mode, inv.requested_provider,
            inv.model, trace.policy.composite_score, trace.policy.policy_version,
            ",".join(h.rule_id for h in trace.policy.rules_fired),
            ",".join(i.tool + (f":{i.entity}" if i.entity else "") for i in trace.tools_invoked),
            trace.duration_ms, trace.model_dump_json(),
        )
        with self._lock:
            self._db.execute("INSERT INTO audit_trace VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            self._db.commit()
        log.info("audit recorded correlation_id=%s decision=%s", trace.correlation_id, trace.decision)

    def get(self, correlation_id: str) -> AuditTrace | None:
        with self._lock:
            row = self._db.execute("SELECT trace_json FROM audit_trace WHERE correlation_id = ?",
                                   (correlation_id,)).fetchone()
        return AuditTrace.model_validate_json(row[0]) if row else None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        cols = ("correlation_id", "created_at", "decision", "planner_mode", "llm_provider", "llm_model",
                "composite_score", "policy_version", "rules_fired", "tools_invoked", "duration_ms")
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(cols)} FROM audit_trace ORDER BY created_at DESC LIMIT ?",
                                    (limit,)).fetchall()
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
