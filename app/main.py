"""
Marge — AI Pastoral Assistant
FastAPI entry point.

Start with:
  cd /root/marge && uvicorn app.main:app --reload

API docs:
  http://localhost:8000/docs      (Swagger UI)
  http://localhost:8000/redoc     (ReDoc)
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

from app.database import init_db
from app.routers import assistant, briefing, visitors, members, care, drafts
from app.routers import chat
from app.runtime_config import assert_production_runtime_safe
from app.services.accounts import session_cookie_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("marge")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database on startup."""
    logger.info("Marge is waking up. Initializing database…")
    assert_production_runtime_safe()
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
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def marge_session_cookie_to_header(request: Request, call_next):
    """
    Allow same-origin browser sessions to use an HttpOnly cookie while keeping
    existing routers and MCP/API clients on X-Marge-Account-Token.
    """
    has_header = any(key.lower() == b"x-marge-account-token" for key, _value in request.scope.get("headers", []))
    if not has_header:
        token = request.cookies.get(session_cookie_name())
        if token:
            headers = list(request.scope.get("headers", []))
            headers.append((b"x-marge-account-token", token.encode("latin-1")))
            request.scope["headers"] = headers
    return await call_next(request)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(briefing.router)
app.include_router(assistant.router)
app.include_router(visitors.router)
app.include_router(members.router)
app.include_router(care.router)
app.include_router(drafts.router)
app.include_router(chat.router)

# ── Static files (frontend) ───────────────────────────────────────────────────

import os as _os
_frontend_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend")
if _os.path.isdir(_frontend_dir):
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["root"])
def root():
    """
    Public bootstrap pointer.

    Keep this generic: no pastor or church identity should be exposed before a
    workspace session exists.
    """
    return {
        "status": "ok",
        "message": "Marge is running. Create or resume a private workspace before adding pastoral data.",
        "version": "0.1.0",
        "app": "/app",
        "docs": "/docs",
    }


@app.get("/health", tags=["root"])
def health():
    """Simple health check endpoint."""
    return {"status": "healthy"}
