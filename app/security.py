"""Security configuration and runtime protections for Marge."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Tuple

from fastapi import HTTPException, Request, status

logger = logging.getLogger("marge.security")


TRUTHY_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CorsSettings:
    allow_origins: List[str]
    allow_methods: List[str]
    allow_headers: List[str]
    allow_credentials: bool


@dataclass(frozen=True)
class RateLimitSettings:
    enabled: bool
    window_seconds: int
    chat_requests: int
    write_requests: int
    block_seconds: int
    max_breaches: int


class EnvironmentValidationError(RuntimeError):
    """Raised when environment configuration is insecure or incomplete."""


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUTHY_VALUES


def _parse_csv(env_name: str, default: str) -> List[str]:
    raw = os.getenv(env_name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_environment() -> str:
    return os.getenv("APP_ENV", "development").strip().lower()


def get_cors_settings() -> CorsSettings:
    env = get_environment()

    if env == "production":
        origins = _parse_csv("CORS_ORIGINS", "")
        methods = _parse_csv("CORS_METHODS", "GET,POST,PATCH,DELETE,OPTIONS")
        headers = _parse_csv("CORS_HEADERS", "Authorization,Content-Type")
        allow_credentials = _as_bool(os.getenv("CORS_ALLOW_CREDENTIALS"), default=False)
    else:
        origins = _parse_csv("CORS_ORIGINS", "*")
        methods = _parse_csv("CORS_METHODS", "GET,POST,PATCH,DELETE,OPTIONS")
        headers = _parse_csv("CORS_HEADERS", "Authorization,Content-Type")
        allow_credentials = _as_bool(os.getenv("CORS_ALLOW_CREDENTIALS"), default=True)

    return CorsSettings(
        allow_origins=origins,
        allow_methods=methods,
        allow_headers=headers,
        allow_credentials=allow_credentials,
    )


def get_rate_limit_settings() -> RateLimitSettings:
    return RateLimitSettings(
        enabled=_as_bool(os.getenv("RATE_LIMIT_ENABLED"), default=False),
        window_seconds=max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))),
        chat_requests=max(1, int(os.getenv("RATE_LIMIT_CHAT_REQUESTS", "30"))),
        write_requests=max(1, int(os.getenv("RATE_LIMIT_WRITE_REQUESTS", "120"))),
        block_seconds=max(1, int(os.getenv("ABUSE_BLOCK_SECONDS", "600"))),
        max_breaches=max(1, int(os.getenv("ABUSE_MAX_BREACHES", "5"))),
    )


def validate_environment() -> None:
    """Fail fast if required production configuration is insecure or missing."""
    env = get_environment()
    cors = get_cors_settings()

    if env != "production":
        return

    errors: List[str] = []

    if not cors.allow_origins:
        errors.append("CORS_ORIGINS must be set in production with explicit allowed origins.")

    if "*" in cors.allow_origins:
        errors.append("CORS_ORIGINS cannot contain '*' in production.")

    if cors.allow_credentials and "*" in cors.allow_origins:
        errors.append("CORS_ALLOW_CREDENTIALS=true is incompatible with wildcard CORS origins.")

    for required_env in ["DATABASE_URL"]:
        if not os.getenv(required_env):
            errors.append(f"{required_env} is required in production.")

    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite"):
        errors.append("DATABASE_URL cannot use SQLite in production; use PostgreSQL.")

    if os.getenv("PASTOR_NAME", "").strip().lower() in {"", "pastor", "nathan"}:
        errors.append("PASTOR_NAME must be explicitly configured for production.")

    if os.getenv("CHURCH_NAME", "").strip().lower() in {"", "your church", "hallmark church"}:
        errors.append("CHURCH_NAME must be explicitly configured for production.")

    if errors:
        joined = " ".join(errors)
        raise EnvironmentValidationError(joined)

    logger.info("Production environment validation passed.")


class InMemoryAbuseProtector:
    """Simple in-memory rate limiter for chat and write-heavy requests."""

    def __init__(self, settings: RateLimitSettings):
        self.settings = settings
        self._lock = threading.Lock()
        self._requests: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._breaches: Dict[str, int] = defaultdict(int)
        self._blocked_until: Dict[str, float] = {}

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        ip = forwarded or (request.client.host if request.client else "unknown")
        return ip

    def _bucket_for_request(self, request: Request) -> str | None:
        path = request.url.path
        method = request.method.upper()

        if method == "POST" and path.startswith("/chat"):
            return "chat"

        if method in {"POST", "PATCH", "PUT", "DELETE"} and (
            path.startswith("/members")
            or path.startswith("/visitors")
            or path.startswith("/care")
        ):
            return "write"

        return None

    def check(self, request: Request) -> None:
        if not self.settings.enabled:
            return

        bucket = self._bucket_for_request(request)
        if not bucket:
            return

        client = self._client_key(request)
        now = time.time()

        with self._lock:
            blocked_until = self._blocked_until.get(client)
            if blocked_until and blocked_until > now:
                retry_after = int(blocked_until - now)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limited due to repeated abuse. Retry in {retry_after} seconds.",
                )

            key = (client, bucket)
            window_start = now - self.settings.window_seconds
            bucket_events = self._requests[key]

            while bucket_events and bucket_events[0] < window_start:
                bucket_events.popleft()

            limit = self.settings.chat_requests if bucket == "chat" else self.settings.write_requests
            if len(bucket_events) >= limit:
                self._breaches[client] += 1
                if self._breaches[client] >= self.settings.max_breaches:
                    self._blocked_until[client] = now + self.settings.block_seconds
                    logger.warning("Blocked abusive client %s for %s seconds", client, self.settings.block_seconds)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please slow down and retry shortly.",
                )

            bucket_events.append(now)
