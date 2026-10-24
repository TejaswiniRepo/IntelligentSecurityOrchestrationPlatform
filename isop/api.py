"""FastAPI entry point. Swagger UI at /docs.

Run:  uvicorn isop.api:app --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from isop import __version__
from isop.audit import AuditSink
from isop.config import Settings
from isop.orchestrator import InvestigationService
from isop.planner import build_planner
from isop.policy import PolicyConfig, PolicyEngine
from isop.schemas import AuditTrace, RegistrationCheckRequest, RegistrationCheckResponse
from isop.tool_client import McpToolClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast on bad configuration before accepting traffic.
        policy = PolicyEngine(PolicyConfig.load(settings.policy_path))
        planner = build_planner(settings.planner)
        audit = AuditSink(settings.audit_path)
        async with McpToolClient(settings.tool_transport, settings.tool_timeout_s) as tools:
            app.state.service = InvestigationService(tools, planner, policy, audit, settings.max_follow_ups)
            app.state.audit = audit
            yield

    app = FastAPI(
        title="ISOP — Intelligent Security Orchestration Platform",
        version=__version__,
        description="Registration risk check. All signals in this MVP are **simulated** (see `simulated` flags).",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body echoes submitted values (including passwords). Strip them.
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.post("/register/check", response_model=RegistrationCheckResponse, tags=["registration"])
    async def register_check(body: RegistrationCheckRequest, request: Request) -> RegistrationCheckResponse:
        return await request.app.state.service.check(body)

    @app.get("/audit/{correlation_id}", response_model=AuditTrace, tags=["audit"])
    async def get_audit(correlation_id: str, request: Request) -> AuditTrace:
        trace = request.app.state.audit.get(correlation_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="unknown correlation_id")
        return trace

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "planner": settings.planner, "tool_transport": settings.tool_transport}

    return app


app = create_app()
