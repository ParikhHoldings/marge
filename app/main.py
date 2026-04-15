"""
Marge — AI Pastoral Assistant
FastAPI entry point.

Start with:
  cd /root/marge && uvicorn app.main:app --reload

API docs:
  http://localhost:8000/docs      (Swagger UI)
  http://localhost:8000/redoc     (ReDoc)
"""

import logging
import os
import time
import uuid
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

from app.database import init_db
from app.routers import briefing, visitors, members, care
from app.routers import chat
from app.observability import (
    ContextFilter,
    inc_counter,
    set_request_context,
    snapshot_metrics,
    observe_latency,
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "tenant_id": getattr(record, "tenant_id", "-"),
            "church_id": getattr(record, "church_id", "-"),
        }
        if hasattr(record, "route"):
            payload["route"] = record.route
        if hasattr(record, "method"):
            payload["method"] = record.method
        if hasattr(record, "status_code"):
            payload["status_code"] = record.status_code
        if hasattr(record, "latency_ms"):
            payload["latency_ms"] = record.latency_ms
        if hasattr(record, "error_class"):
            payload["error_class"] = record.error_class
        return json.dumps(payload)


root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if not root_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)
for handler in root_logger.handlers:
    handler.addFilter(ContextFilter())

logger = logging.getLogger("marge")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database on startup."""
    logger.info("Marge is waking up. Initializing database…")
    init_db()
    logger.info("Database ready. Good morning, Pastor.")
    yield
    logger.info("Marge signing off.")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Marge — AI Pastoral Assistant",
    description=(
        "Marge is the AI church secretary every solo pastor never had. "
        "She shows up every morning with the people your pastor needs to care for today — "
        "and helps him do it."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow all origins in dev; tighten in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(briefing.router)
app.include_router(visitors.router)
app.include_router(members.router)
app.include_router(care.router)
app.include_router(chat.router)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    tenant_id = request.headers.get("X-Tenant-ID")
    church_id = request.headers.get("X-Church-ID")
    set_request_context(request_id=request_id, tenant_id=tenant_id, church_id=church_id)
    request.state.request_id = request_id
    start = time.perf_counter()
    route_path = request.url.path
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        status_code = 500
        inc_counter("http_errors_total", route=route_path, error_class=exc.__class__.__name__)
        raise
    finally:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        observe_latency("http_request_latency_ms", latency_ms)
        logger.info(
            "request_complete",
            extra={
                "route": route_path,
                "method": request.method,
                "status_code": status_code,
                "latency_ms": latency_ms,
            },
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    path = request.url.path
    error_class = exc.__class__.__name__
    if path.startswith("/care"):
        inc_counter("care_prayer_crud_failures_total", route=path, method=request.method, error_class=error_class)
    logger.error(
        "unhandled_exception",
        extra={"route": path, "method": request.method, "status_code": 500, "error_class": error_class},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": request.state.request_id})


@app.exception_handler(HTTPException)
async def http_exception_passthrough(request: Request, exc: HTTPException):
    if request.url.path.startswith("/care") and exc.status_code >= 500:
        inc_counter("care_prayer_crud_failures_total", route=request.url.path, method=request.method, error_class=exc.__class__.__name__)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_exception_passthrough(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/care"):
        inc_counter("care_prayer_crud_failures_total", route=request.url.path, method=request.method, error_class=exc.__class__.__name__)
    return await request_validation_exception_handler(request, exc)

# ── Static files (frontend) ───────────────────────────────────────────────────

import os as _os
_frontend_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend")
if _os.path.isdir(_frontend_dir):
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["root"])
def root():
    """
    Health check and welcome.

    Marge is running if you see this.
    """
    pastor_name = os.getenv("PASTOR_NAME", "Pastor")
    church_name = os.getenv("CHURCH_NAME", "your church")
    return {
        "status": "ok",
        "message": f"Good morning, {pastor_name}. Marge is ready.",
        "church": church_name,
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health", tags=["root"])
def health():
    """Simple health check endpoint."""
    return {"status": "healthy"}


@app.get("/metrics/workflows", tags=["root"])
def workflow_metrics():
    """Expose in-memory workflow counters and latency aggregates."""
    return snapshot_metrics()
