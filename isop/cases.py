"""Registration cases and their persisted investigation events.

A case is created when a registration is submitted. The investigation runs as a server-side task,
independent of any browser connection; every step is appended to `case_event` so a UI can stream it
live (SSE), poll it, or rebuild the full timeline after a refresh. Event details are already sanitised
by the orchestrator: masked identifiers only, never passwords.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isop.audit import mask_email
from isop.schemas import RegistrationCheckRequest, RegistrationCheckResponse

log = logging.getLogger("isop.cases")

TERMINAL = frozenset({"completed", "failed", "interrupted"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS registration_case (
    case_id          TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    status           TEXT NOT NULL,
    email_masked     TEXT NOT NULL,
    decision         TEXT,
    planner_mode     TEXT,
    composite_score  REAL,
    duration_ms      REAL,
    tools_invoked    INTEGER,
    error            TEXT,
    idempotency_key  TEXT UNIQUE,
    payload_hash     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_case_created ON registration_case(created_at);
CREATE TABLE IF NOT EXISTS case_event (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      TEXT NOT NULL REFERENCES registration_case(case_id),
    seq          INTEGER NOT NULL,
    type         TEXT NOT NULL,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,
    ts           TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_case ON case_event(case_id, event_id);
"""

CASE_COLUMNS = ("case_id", "created_at", "updated_at", "status", "email_masked", "decision", "planner_mode",
                "composite_score", "duration_ms", "tools_invoked", "error")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_case_id() -> str:
    return f"REG-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(4).upper()}"


def payload_fingerprint(req: RegistrationCheckRequest) -> str:
    """Hash of the submission for idempotency checks. The password is never part of it."""
    data = req.model_dump(mode="json", exclude={"password"})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class IdempotencyConflictError(Exception):
    """Same Idempotency-Key reused with a different payload."""


class CaseStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ------------------------------------------------------------------ cases

    def create(self, case_id: str, email_masked: str, payload_hash: str, idempotency_key: str | None) -> None:
        ts = now_iso()
        with self._lock:
            self._db.execute(
                "INSERT INTO registration_case (case_id, created_at, updated_at, status, email_masked,"
                " idempotency_key, payload_hash) VALUES (?,?,?,?,?,?,?)",
                (case_id, ts, ts, "running", email_masked, idempotency_key, payload_hash))
            self._db.commit()

    def find_by_key(self, idempotency_key: str) -> tuple[dict[str, Any], str] | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {', '.join(CASE_COLUMNS)}, payload_hash FROM registration_case WHERE idempotency_key = ?",
                (idempotency_key,)).fetchone()
        return (dict(zip(CASE_COLUMNS, row[:-1], strict=True)), row[-1]) if row else None

    def finish(self, case_id: str, status: str, *, decision: str | None = None, planner_mode: str | None = None,
               composite_score: float | None = None, duration_ms: float | None = None,
               tools_invoked: int | None = None, error: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE registration_case SET status=?, updated_at=?, decision=?, planner_mode=?, composite_score=?,"
                " duration_ms=?, tools_invoked=?, error=? WHERE case_id=?",
                (status, now_iso(), decision, planner_mode, composite_score, duration_ms, tools_invoked, error,
                 case_id))
            self._db.commit()

    def get(self, case_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(f"SELECT {', '.join(CASE_COLUMNS)} FROM registration_case WHERE case_id = ?",
                                   (case_id,)).fetchone()
        return dict(zip(CASE_COLUMNS, row, strict=True)) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(CASE_COLUMNS)} FROM registration_case ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(zip(CASE_COLUMNS, r, strict=True)) for r in rows]

    def mark_interrupted(self) -> list[str]:
        """Cases left 'running' by a previous process can never finish; close them honestly."""
        with self._lock:
            ids = [r[0] for r in self._db.execute(
                "SELECT case_id FROM registration_case WHERE status = 'running'").fetchall()]
        for cid in ids:
            self.append(cid, "case.failed", "decision", "failed",
                        {"error": "service restarted before the investigation finished"})
            self.finish(cid, "interrupted", error="service restarted before the investigation finished")
        return ids

    # ------------------------------------------------------------------ events

    def append(self, case_id: str, type_: str, stage: str, status: str, details: dict[str, Any]) -> dict[str, Any]:
        ts = now_iso()
        body = json.dumps(details, default=str)
        with self._lock:
            seq = self._db.execute("SELECT COUNT(*) FROM case_event WHERE case_id = ?", (case_id,)).fetchone()[0] + 1
            cur = self._db.execute(
                "INSERT INTO case_event (case_id, seq, type, stage, status, ts, details_json) VALUES (?,?,?,?,?,?,?)",
                (case_id, seq, type_, stage, status, ts, body))
            self._db.commit()
            event_id = cur.lastrowid
        return {"event_id": event_id, "case_id": case_id, "seq": seq, "type": type_, "stage": stage,
                "status": status, "ts": ts, "details": details}

    def events(self, case_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT event_id, seq, type, stage, status, ts, details_json FROM case_event"
                " WHERE case_id = ? AND event_id > ? ORDER BY event_id", (case_id, after)).fetchall()
        return [{"event_id": r[0], "case_id": case_id, "seq": r[1], "type": r[2], "stage": r[3], "status": r[4],
                 "ts": r[5], "details": json.loads(r[6])} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


class EventHub:
    """Wakes stream readers when any event is appended (single process, asyncio).

    Readers capture `version` BEFORE reading the store and pass it to `wait`; if anything was appended in
    between, `wait` returns at once. Without this, an event emitted between the read and the wait would be
    missed until the next event (a lost wake-up that delayed `planner.started` by a whole LLM call).
    """

    def __init__(self) -> None:
        self.version = 0
        self._event = asyncio.Event()

    def notify(self) -> None:
        self.version += 1
        self._event.set()
        self._event = asyncio.Event()

    async def wait(self, since: int, timeout: float) -> None:
        if self.version != since:
            return
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            pass


Emit = Callable[[str, str, str, dict[str, Any]], None]


class InvestigationRunner:
    """Creates cases and runs investigations as server-side tasks that outlive the HTTP request."""

    def __init__(self, service: Any, store: CaseStore, hub: EventHub, max_concurrent: int = 8) -> None:
        self.service = service
        self.store = store
        self.hub = hub
        self.max_concurrent = max_concurrent  # CPU-bound local LLM: bound the queue instead of piling up calls
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def busy(self) -> bool:
        return len(self._tasks) >= self.max_concurrent

    def emitter(self, case_id: str) -> Emit:
        def emit(type_: str, stage: str, status: str, details: dict[str, Any]) -> None:
            self.store.append(case_id, type_, stage, status, details)
            self.hub.notify()
        return emit

    def open_case(self, req: RegistrationCheckRequest, idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
        """Return (case, created). A repeated Idempotency-Key returns the original case instead of a new run."""
        digest = payload_fingerprint(req)
        if idempotency_key:
            found = self.store.find_by_key(idempotency_key)
            if found:
                case, stored_digest = found
                if stored_digest != digest:
                    raise IdempotencyConflictError("Idempotency-Key was already used for a different registration")
                return case, False
        for _ in range(3):  # new_case_id has 32 random bits per day; retry on the unlikely collision
            case_id = new_case_id()
            try:
                self.store.create(case_id, mask_email(str(req.email).lower()), digest, idempotency_key)
                break
            except sqlite3.IntegrityError:
                if idempotency_key and self.store.find_by_key(idempotency_key):
                    return self.open_case(req, idempotency_key)
        else:  # pragma: no cover
            raise RuntimeError("could not allocate a case id")
        provided = [k for k, v in req.model_dump(exclude={"password"}).items() if v not in (None, "")]
        self.emitter(case_id)("case.received", "request", "completed", {
            "case_id": case_id, "fields_validated": provided, "password_requested": False,
            "password_received_and_discarded": req.password is not None,
            "idempotency_key_supplied": idempotency_key is not None,
        })
        return self.store.get(case_id) or {}, True

    def start(self, case_id: str, req: RegistrationCheckRequest) -> None:
        task = asyncio.create_task(self.execute(case_id, req), name=f"investigation-{case_id}")
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning("investigation task ended with %s", type(task.exception()).__name__)

    async def execute(self, case_id: str, req: RegistrationCheckRequest) -> RegistrationCheckResponse:
        emit = self.emitter(case_id)
        try:
            resp: RegistrationCheckResponse = await self.service.check(req, case_id=case_id, emit=emit)
        except asyncio.CancelledError:
            emit("case.failed", "decision", "failed", {"error": "service stopped before the investigation finished"})
            self.store.finish(case_id, "interrupted", error="service stopped before the investigation finished")
            raise
        except Exception as exc:
            message = str(exc) if type(exc).__name__ == "PlannerUnavailableError" else \
                f"internal error ({type(exc).__name__})"
            log.exception("investigation failed case_id=%s", case_id)
            emit("case.failed", "decision", "failed", {"error": message})
            self.store.finish(case_id, "failed", error=message)
            raise
        t = resp.trace
        emit("case.completed", "decision", "completed", {
            "decision": resp.decision.value, "reason_codes": resp.reason_codes,
            "composite_score": resp.composite_score, "policy_version": resp.policy_version,
            "duration_ms": t.duration_ms, "tools_invoked": len(t.tools_invoked), "signals_evaluated": len(t.signals),
            "planner_mode": resp.planner_mode, "llm_provider": t.investigation.requested_provider,
            "llm_model": t.investigation.model, "llm_calls": t.investigation.llm_calls,
            "fallback_used": t.investigation.fallback_used, "simulated_signals": resp.simulated_signals,
        })
        self.store.finish(case_id, "completed", decision=resp.decision.value, planner_mode=resp.planner_mode,
                          composite_score=resp.composite_score, duration_ms=t.duration_ms,
                          tools_invoked=len(t.tools_invoked))
        return resp

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
