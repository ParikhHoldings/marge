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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

from app.database import init_db
from app.routers import briefing, visitors, members, care
from app.routers import chat

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
