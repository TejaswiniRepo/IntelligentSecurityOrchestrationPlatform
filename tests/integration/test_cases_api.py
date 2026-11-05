"""Async registration cases, persisted events, SSE streaming and the dashboard routes."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isop.api import create_app
from isop.cases import CaseStore, EventHub
from isop.demo import SCENARIOS
from isop.llm import LlmError
from tests.conftest import FakeLlmProvider, make_settings, payload

UI_PAYLOAD = {"first_name": "Alice", "last_name": "Walker", "email": "alice.walker@gmail.com",
              "phone": "+14155550123", "country": "US", "ip_address": "192.0.2.10", "device_id": "dev-a1b2c3"}


def wait_done(c: TestClient, case_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        case = c.get(f"/registrations/{case_id}").json()
        if case["status"] != "running":
            return case
        time.sleep(0.05)
    raise AssertionError(f"case {case_id} still running after {timeout}s")


def submit(c: TestClient, body: dict, key: str | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    return c.post("/register/check?mode=async", json=body, headers=headers)


def test_async_submit_returns_case_id_and_runs_in_background(client: TestClient) -> None:
    r = submit(client, UI_PAYLOAD)
    assert r.status_code == 202
    body = r.json()
    assert re.fullmatch(r"REG-\d{8}-[0-9A-F]{8}", body["case_id"]) and body["created"] is True
    assert set(body["links"]) == {"case", "events", "stream", "result"}
    case = wait_done(client, body["case_id"])
    assert (case["status"], case["decision"], case["planner_mode"]) == ("completed", "ALLOW", "deterministic")
    result = client.get(f"/registrations/{body['case_id']}/result")
    assert result.status_code == 200 and result.json()["decision"] == "ALLOW"
    assert client.get("/registrations").json()[0]["case_id"] == body["case_id"]


def test_event_sequence_covers_every_stage_in_order(client: TestClient) -> None:
    case_id = submit(client, {**UI_PAYLOAD, "device_id": "DEV-789"}).json()["case_id"]
    wait_done(client, case_id)
    events = client.get(f"/registrations/{case_id}/events").json()["events"]
    types = [e["type"] for e in events]
    assert types[:3] == ["case.received", "context.prepared", "baseline.started"]
    assert types[-4:] == ["policy.started", "policy.completed", "audit.recorded", "case.completed"]
    assert {"planner.started", "planner.completed", "planner.concluded", "aggregation.completed"} <= set(types)
    ids = [e["event_id"] for e in events]
    assert ids == sorted(ids) and [e["seq"] for e in events] == list(range(1, len(events) + 1))
    follow = [e for e in events if e["type"] == "tool.completed" and e["stage"] == "follow_up"]
    assert [e["details"]["tool"] for e in follow] == ["check_device_history"]
    assert follow[0]["details"]["signal"]["simulated"] is True
    final = events[-1]["details"]
    assert final["decision"] == "REJECT" and final["tools_invoked"] == 5 and final["llm_provider"] == "deterministic"
    assert all(e["ts"] and e["case_id"] == case_id for e in events)


def test_events_are_sanitised(client: TestClient) -> None:
    body = {**UI_PAYLOAD, "password": "correct-horse-battery-staple", "device_fingerprint": "fp-3c9a71d2e8b4"}
    case_id = submit(client, body).json()["case_id"]
    wait_done(client, case_id)
    raw = json.dumps(client.get(f"/registrations/{case_id}/events").json())
    for secret in ("correct-horse-battery-staple", "alice.walker@gmail.com", "+14155550123", "192.0.2.10",
                   "dev-a1b2c3", "fp-3c9a71d2e8b4", "Alice", "Walker"):
        assert secret not in raw, f"raw value leaked into events: {secret}"
    assert "a***@gmail.com" in raw and "192.0.2.0/24" in raw
    received = client.get(f"/registrations/{case_id}/events").json()["events"][0]["details"]
    assert received["password_received_and_discarded"] is True and "password" not in received["fields_validated"]


def test_sse_stream_delivers_all_events_then_ends_and_resumes(client: TestClient) -> None:
    case_id = submit(client, UI_PAYLOAD).json()["case_id"]
    streamed, end = [], None
    with client.stream("GET", f"/registrations/{case_id}/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event == "progress":
                streamed.append(json.loads(line[5:]))
            elif line.startswith("data:") and event == "end":
                end = json.loads(line[5:])
                break
    stored = client.get(f"/registrations/{case_id}/events").json()["events"]
    assert [e["event_id"] for e in streamed] == [e["event_id"] for e in stored]
    assert end == {"status": "completed", "type": "case.completed"}

    # Reconnect with Last-Event-ID: only later events come back, no duplicates.
    cut = stored[len(stored) // 2]["event_id"]
    with client.stream("GET", f"/registrations/{case_id}/stream", headers={"Last-Event-ID": str(cut)}) as r:
        resumed = [json.loads(l[5:])["event_id"] for l in r.iter_lines() if l.startswith("data:") and "event_id" in l]
    assert resumed == [e["event_id"] for e in stored if e["event_id"] > cut]


def test_polling_endpoint_supports_after_cursor(client: TestClient) -> None:
    case_id = submit(client, UI_PAYLOAD).json()["case_id"]
    wait_done(client, case_id)
    all_ev = client.get(f"/registrations/{case_id}/events").json()["events"]
    later = client.get(f"/registrations/{case_id}/events?after={all_ev[4]['event_id']}").json()["events"]
    assert [e["event_id"] for e in later] == [e["event_id"] for e in all_ev[5:]]


def test_duplicate_submission_with_same_key_returns_original_case(client: TestClient) -> None:
    first = submit(client, UI_PAYLOAD, key="dup-key-0001").json()
    second = submit(client, UI_PAYLOAD, key="dup-key-0001")
    assert second.status_code == 202 and second.json()["case_id"] == first["case_id"]
    assert second.json()["created"] is False
    wait_done(client, first["case_id"])
    assert len([r for r in client.get("/registrations").json() if r["case_id"] == first["case_id"]]) == 1
    received = [e for e in client.get(f"/registrations/{first['case_id']}/events").json()["events"]
                if e["type"] == "case.received"]
    assert len(received) == 1  # the investigation ran once


def test_reused_key_with_different_payload_is_409_and_bad_key_is_400(client: TestClient) -> None:
    submit(client, UI_PAYLOAD, key="dup-key-0002")
    assert submit(client, {**UI_PAYLOAD, "email": "other@gmail.com"}, key="dup-key-0002").status_code == 409
    assert submit(client, UI_PAYLOAD, key="bad key!").status_code == 400


def test_concurrent_cases_keep_their_own_events(client: TestClient) -> None:
    bodies = [UI_PAYLOAD, {**UI_PAYLOAD, "ip_address": "203.0.113.7"}, {**UI_PAYLOAD, "device_id": "DEV-789"}]
    ids = [submit(client, b).json()["case_id"] for b in bodies]  # all three start before any finishes
    results = [wait_done(client, i)["decision"] for i in ids]
    assert results == ["ALLOW", "REVIEW", "REJECT"]
    for case_id in ids:
        events = client.get(f"/registrations/{case_id}/events").json()["events"]
        assert {e["case_id"] for e in events} == {case_id}
        assert [e["type"] for e in events].count("case.completed") == 1


def test_model_failure_with_fallback_error_marks_case_failed(tmp_path: Path) -> None:
    fake = FakeLlmProvider(LlmError("model_missing", "model 'qwen2.5:3b' not installed"))
    app = create_app(make_settings(tmp_path, llm_provider="ollama", llm_fallback="error"), llm_provider=fake)
    with TestClient(app) as c:
        case_id = submit(c, UI_PAYLOAD).json()["case_id"]
        case = wait_done(c, case_id)
        assert case["status"] == "failed" and "LLM_FALLBACK=error" in case["error"]
        last = c.get(f"/registrations/{case_id}/events").json()["events"][-1]
        assert last["type"] == "case.failed"
        assert c.get(f"/registrations/{case_id}/result").status_code == 409


def test_llm_mode_events_label_the_model_and_its_choices(tmp_path: Path) -> None:
    fake = FakeLlmProvider({"assessment": "Device seen before.", "done": True,
                            "steps": [{"action": "check_device_history", "reason": "device seen before"}]})
    with TestClient(create_app(make_settings(tmp_path, llm_provider="ollama"), llm_provider=fake)) as c:
        case_id = submit(c, {**UI_PAYLOAD, "device_id": "DEV-789"}).json()["case_id"]
        wait_done(c, case_id)
        events = c.get(f"/registrations/{case_id}/events").json()["events"]
    turn = next(e for e in events if e["type"] == "planner.completed")["details"]
    assert (turn["mode"], turn["llm_status"], turn["assessment"]) == ("llm", "ok", "Device seen before.")
    started = next(e for e in events if e["type"] == "tool.started" and e["stage"] == "follow_up")["details"]
    assert started["requested_by"] == "llm" and started["rationale"] == "device seen before"


def test_unknown_or_malformed_case_ids_are_404(client: TestClient) -> None:
    for path in ("/registrations/REG-20260101-DEADBEEF", "/registrations/../../etc/passwd",
                 "/registrations/REG-20260101-DEADBEEF/events", "/registrations/x/stream"):
        assert client.get(path).status_code == 404, path


def test_running_case_from_previous_process_is_marked_interrupted(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = CaseStore(settings.audit_db_path)
    store.create("REG-20260101-0000ABCD", "a***@x.com", "hash", None)
    store.close()
    with TestClient(create_app(settings)) as c:
        case = c.get("/registrations/REG-20260101-0000ABCD").json()
        assert case["status"] == "interrupted"
        assert c.get("/registrations/REG-20260101-0000ABCD/events").json()["events"][-1]["type"] == "case.failed"


def test_sync_mode_still_returns_the_full_decision_and_records_a_case(client: TestClient) -> None:
    r = client.post("/register/check", json=payload())
    assert r.status_code == 200 and r.json()["decision"] == "ALLOW"
    case_id = r.json()["correlation_id"]
    assert client.get(f"/registrations/{case_id}").json()["status"] == "completed"


def test_ui_fields_without_password_or_country_are_accepted(client: TestClient) -> None:
    body = {k: v for k, v in UI_PAYLOAD.items() if k != "country"}
    r = client.post("/register/check", json=body)
    assert r.status_code == 200
    phone = next(s for s in r.json()["trace"]["signals"] if s["signal_type"] == "PHONE")
    assert "COUNTRY_NOT_SUPPLIED" in phone["reason_codes"] and "PHONE_COUNTRY_MISMATCH" not in phone["reason_codes"]


@pytest.mark.parametrize("bad", [{"first_name": "R2-D2"}, {"last_name": "<b>x</b>"}, {"device_fingerprint": "short"}])
def test_ui_field_validation(client: TestClient, bad: dict) -> None:
    assert client.post("/register/check", json={**UI_PAYLOAD, **bad}).status_code == 422


def test_demo_scenarios_produce_their_expected_decisions(client: TestClient) -> None:
    listed = client.get("/demo/scenarios").json()
    assert {s["id"] for s in listed} == {s["id"] for s in SCENARIOS}
    for s in listed:
        assert client.post("/register/check", json=s["payload"]).json()["decision"] == s["expected"], s["id"]


def test_dashboard_is_served_with_strict_headers(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
    r = client.get("/ui/")
    assert r.status_code == 200 and "Show Intelligent Security Orchestration" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and r.headers["cache-control"] == "no-cache"
    for asset in ("/ui/js/app.js", "/ui/js/store.js", "/ui/js/theme.js", "/ui/css/app.css"):
        assert client.get(asset).status_code == 200, asset
    assert '<script src="js/theme.js"></script>' in r.text  # external: the strict CSP forbids inline scripts
    css = client.get("/ui/css/app.css").text
    assert ':root[data-theme="dark"]' in css and "color-scheme: dark" in css
    assert "cdn" not in r.text.lower()


@pytest.mark.anyio
async def test_event_hub_has_no_lost_wakeups() -> None:
    hub = EventHub()
    seen = hub.version
    hub.notify()  # an event lands between the reader's read and its wait
    started = time.monotonic()
    await hub.wait(seen, timeout=5)
    assert time.monotonic() - started < 0.5  # returns at once instead of sleeping until the next event

    seen = hub.version
    waiter = asyncio.create_task(hub.wait(seen, timeout=5))
    await asyncio.sleep(0.01)
    hub.notify()
    await asyncio.wait_for(waiter, 1)


def test_concurrency_cap_returns_429_but_duplicates_still_resolve(client: TestClient) -> None:
    first = submit(client, UI_PAYLOAD, key="cap-key-0001").json()
    wait_done(client, first["case_id"])
    client.app.state.runner.max_concurrent = 0  # simulate a full queue
    r = submit(client, UI_PAYLOAD)
    assert r.status_code == 429 and r.headers["retry-after"] == "5"
    assert submit(client, UI_PAYLOAD, key="cap-key-0001").status_code == 202  # a retry of a known case is fine
