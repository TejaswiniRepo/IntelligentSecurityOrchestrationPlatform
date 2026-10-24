"""Audit trail: append-only JSONL plus a bounded in-memory index for GET /audit/{id}.

Only masked identifiers are recorded. Passwords never reach this module (see InvestigationSubject).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from ipaddress import ip_address, ip_network
from pathlib import Path

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


def mask_arguments(arguments: dict[str, str]) -> dict[str, str]:
    return {k: MASKERS.get(k, lambda v: v)(v) for k, v in arguments.items()}


class AuditSink:
    def __init__(self, path: Path, keep_in_memory: int = 500) -> None:
        self.path = path
        self._recent: OrderedDict[str, AuditTrace] = OrderedDict()
        self._keep = keep_in_memory
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, trace: AuditTrace) -> None:
        line = trace.model_dump_json()
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._recent[trace.correlation_id] = trace
            while len(self._recent) > self._keep:
                self._recent.popitem(last=False)
        log.info("audit recorded correlation_id=%s decision=%s", trace.correlation_id, trace.decision)

    def get(self, correlation_id: str) -> AuditTrace | None:
        with self._lock:
            return self._recent.get(correlation_id)
