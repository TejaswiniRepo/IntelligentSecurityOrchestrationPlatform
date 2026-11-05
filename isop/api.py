"""FastAPI entry point. Dashboard at /ui/, Swagger UI at /docs.

Run:  python -m uvicorn isop.api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from isop import __version__
from isop.audit import AuditSink
from isop.cases import CaseStore, EventHub, IdempotencyConflictError, InvestigationRunner
from isop.config import Settings
from isop.demo import SCENARIOS
from isop.llm import build_llm_provider
from isop.orchestrator import InvestigationService, PlannerUnavailableError
from isop.planner import build_planner
from isop.policy import PolicyConfig, PolicyEngine
from isop.schemas import AuditTrace, RegistrationCheckRequest, RegistrationCheckResponse
from isop.tool_client import McpToolClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("isop.api")
STATIC_DIR = Path(__file__).parent / "static"
CASE_ID = re.compile(r"^REG-\d{8}-[0-9A-F]{8}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FINAL_EVENTS = frozenset({"case.completed", "case.failed"})
UI_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
          "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class SecurityHeaders:
    """Pure ASGI middleware (does not buffer the SSE stream). Strict CSP on the dashboard only:
    the bundled Swagger page needs an inline script."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        is_ui = scope["path"].startswith("/ui")

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                if is_ui:
                    # no-cache = revalidate every load (cheap 304s), so an updated dashboard is never stale.
                    headers += [(b"content-security-policy", UI_CSP.encode()), (b"x-frame-options", b"DENY"),
                                (b"cache-control", b"no-cache")]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def result_from_trace(trace: AuditTrace) -> RegistrationCheckResponse:
    return RegistrationCheckResponse(
        correlation_id=trace.correlation_id, decision=trace.decision, reason_codes=trace.policy.reason_codes,
        composite_score=trace.policy.composite_score, policy_version=trace.policy.policy_version,
        simulated_signals=any(s.simulated for s in trace.signals), planner_mode=trace.investigation.effective_mode,
        trace=trace,
    )


def create_app(settings: Settings | None = None, *, llm_provider: Any = None, mcp_server: Any = None) -> FastAPI:
    """`llm_provider` / `mcp_server` let tests inject fakes; production builds them from settings."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast on bad configuration before accepting traffic. Nothing here downloads anything.
        policy = PolicyEngine(PolicyConfig.load(settings.policy_path))
        provider = llm_provider if llm_provider is not None else build_llm_provider(settings)
        planner = build_planner(provider, settings.llm_fallback, settings.llm_cooldown_s)
        audit = AuditSink(settings.audit_db_path)
        store = CaseStore(settings.audit_db_path)
        interrupted = store.mark_interrupted()
        if interrupted:
            log.warning("marked %d unfinished case(s) from a previous run as interrupted", len(interrupted))
        if provider is not None:
            status = await provider.health()
            level = logging.INFO if status.get("model_present", True) and status.get("reachable") is not False \
                else logging.WARNING
            log.log(level, "llm provider status %s (fallback=%s)", status, settings.llm_fallback)
        async with McpToolClient(settings.tool_transport, settings.tool_timeout_s, mcp_server) as tools:
            service = InvestigationService(
                tools, planner, policy, audit, settings.max_follow_ups, settings.llm_max_iterations,
                requested_provider=settings.llm_provider, model=settings.public_summary()["model"],
            )
            hub = EventHub()
            app.state.service, app.state.store, app.state.hub = service, store, hub
            app.state.runner = InvestigationRunner(service, store, hub)
            app.state.audit, app.state.provider, app.state.tools = audit, provider, tools
            app.state.policy_version = policy.config.version
            try:
                yield
            finally:
                await app.state.runner.shutdown()  # cancel while MCP is still connected; cases marked interrupted
                if provider is not None:
                    await provider.aclose()
                store.close()
                audit.close()

    app = FastAPI(
        title="ISOP — Intelligent Security Orchestration Platform",
        version=__version__,
        description="Registration risk check. Signals are **simulated** (see `simulated`). "
                    "The LLM only plans follow-up checks; a deterministic policy decides. Dashboard: `/ui/`.",
        lifespan=lifespan,
        docs_url=None,  # served below from bundled assets so Swagger UI works with no internet
        redoc_url=None,
    )
    app.add_middleware(SecurityHeaders)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/ui", StaticFiles(directory=STATIC_DIR / "ui", html=True), name="ui")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json", title=f"{app.title} - Swagger UI",
            swagger_js_url="/static/swagger/swagger-ui-bundle.js", swagger_css_url="/static/swagger/swagger-ui.css",
            swagger_favicon_url="/static/swagger/favicon-32x32.png",
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body echoes submitted values (including passwords). Strip them.
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(PlannerUnavailableError)
    async def _planner_unavailable(_: Request, exc: PlannerUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def case_or_404(request: Request, case_id: str) -> dict[str, Any]:
        case = request.app.state.store.get(case_id) if CASE_ID.fullmatch(case_id) else None
        if case is None:
            raise HTTPException(status_code=404, detail="unknown case_id")
        return case

    def links(case_id: str) -> dict[str, str]:
        base = f"/registrations/{case_id}"
        return {"case": base, "events": f"{base}/events", "stream": f"{base}/stream", "result": f"{base}/result"}

    # ------------------------------------------------------------------ registration

    @app.post("/register/check", tags=["registration"], response_model=RegistrationCheckResponse,
              responses={202: {"description": "mode=async: investigation started; follow it via the links"}})
    async def register_check(
        body: RegistrationCheckRequest, request: Request,
        mode: str = Query("sync", pattern="^(sync|async)$",
                          description="sync: wait for the decision. async: return the case id at once."),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> Any:
        if idempotency_key is not None and not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise HTTPException(status_code=400, detail="Idempotency-Key must be 8-64 chars of [A-Za-z0-9_-]")
        runner: InvestigationRunner = request.app.state.runner
        if mode == "async" and runner.busy and not (idempotency_key and request.app.state.store.find_by_key(idempotency_key)):
            raise HTTPException(status_code=429, detail=f"{runner.max_concurrent} investigations already running; retry shortly",
                                headers={"Retry-After": "5"})
        try:
            case, created = runner.open_case(body, idempotency_key)
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        case_id = case["case_id"]
        if mode == "async":
            if created:
                runner.start(case_id, body)
            return JSONResponse(status_code=202, content={
                "case_id": case_id, "status": case["status"] if not created else "running",
                "created": created, "links": links(case_id)})
        if created:
            return await runner.execute(case_id, body)
        trace = request.app.state.audit.get(case_id)
        if trace is None:
            raise HTTPException(status_code=409, detail=f"case {case_id} is {case['status']}")
        return result_from_trace(trace)

    @app.get("/registrations", tags=["registration"])
    async def list_registrations(request: Request, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
        return request.app.state.store.list(limit)

    @app.get("/registrations/{case_id}", tags=["registration"])
    async def get_registration(case_id: str, request: Request) -> dict[str, Any]:
        return case_or_404(request, case_id) | {"links": links(case_id)}

    @app.get("/registrations/{case_id}/events", tags=["registration"])
    async def get_events(case_id: str, request: Request, after: int = Query(0, ge=0)) -> dict[str, Any]:
        case = case_or_404(request, case_id)
        return {"case_id": case_id, "status": case["status"],
                "events": request.app.state.store.events(case_id, after)}

    @app.get("/registrations/{case_id}/stream", tags=["registration"])
    async def stream_events(
        case_id: str, request: Request, after: int = Query(0, ge=0),
        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """Server-Sent Events. Resumes after `Last-Event-ID` (browser reconnect) or `after`. Ends after the
        case's final event. Disconnecting never affects the investigation."""
        case_or_404(request, case_id)
        store: CaseStore = request.app.state.store
        hub: EventHub = request.app.state.hub
        cursor = max(after, int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

        async def gen() -> AsyncIterator[str]:
            nonlocal cursor
            yield "retry: 2000\n\n"
            while True:
                seen = hub.version  # captured before the read: no lost wake-ups
                batch = store.events(case_id, cursor)
                for e in batch:
                    cursor = e["event_id"]
                    yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(e, default=str)}\n\n"
                    if e["type"] in FINAL_EVENTS:
                        yield f"event: end\ndata: {json.dumps({'status': e['status'], 'type': e['type']})}\n\n"
                        return
                if not batch and (store.get(case_id) or {}).get("status") in ("completed", "failed", "interrupted"):
                    yield "event: end\ndata: {\"status\": \"already-finished\"}\n\n"
                    return
                if await request.is_disconnected():
                    return
                await hub.wait(seen, 15)
                if hub.version == seen:
                    yield ": keepalive\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/registrations/{case_id}/result", tags=["registration"], response_model=RegistrationCheckResponse,
             responses={202: {"description": "still running"}, 409: {"description": "failed or interrupted"}})
    async def get_result(case_id: str, request: Request) -> Any:
        case = case_or_404(request, case_id)
        if case["status"] == "running":
            return JSONResponse(status_code=202, content={"case_id": case_id, "status": "running"})
        trace = request.app.state.audit.get(case_id)
        if case["status"] != "completed" or trace is None:
            return JSONResponse(status_code=409, content={"case_id": case_id, "status": case["status"],
                                                          "error": case["error"]})
        return result_from_trace(trace)

    @app.get("/demo/scenarios", tags=["demo"])
    async def demo_scenarios() -> list[dict[str, Any]]:
        return SCENARIOS

    # ------------------------------------------------------------------ audit / ops

    @app.get("/audit", tags=["audit"])
    async def list_audit(request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
        return request.app.state.audit.recent(limit)

    @app.get("/audit/{correlation_id}", response_model=AuditTrace, tags=["audit"])
    async def get_audit(correlation_id: str, request: Request) -> AuditTrace:
        trace = request.app.state.audit.get(correlation_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="unknown correlation_id")
        return trace

    @app.get("/health", tags=["ops"])
    async def health(request: Request) -> dict[str, Any]:
        provider = request.app.state.provider
        llm = await provider.health() if provider is not None else {"provider": "deterministic"}
        return {
            "status": "ok",
            "version": __version__,
            "config": settings.public_summary(),
            "llm": llm,
            "mcp_tools": sorted(request.app.state.tools.available_tools),
            "policy_version": request.app.state.policy_version,
        }

    return app


app = create_app()
