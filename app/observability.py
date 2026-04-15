"""
Observability helpers for Marge API and MCP server.

Provides:
- Structured log context helpers (request_id / tenant_id / church_id)
- In-memory workflow metrics with labels
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from typing import Dict, Optional, Tuple

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
tenant_id_ctx: ContextVar[str] = ContextVar("tenant_id", default="-")
church_id_ctx: ContextVar[str] = ContextVar("church_id", default="-")

_METRICS_LOCK = threading.Lock()
_COUNTERS: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], int] = defaultdict(int)
_LATENCY: Dict[str, Dict[str, float | int]] = defaultdict(
    lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
)


def set_request_context(request_id: str, tenant_id: Optional[str], church_id: Optional[str]) -> None:
    request_id_ctx.set(request_id or "-")
    tenant_id_ctx.set(tenant_id or "-")
    church_id_ctx.set(church_id or "-")


def get_request_context() -> dict:
    return {
        "request_id": request_id_ctx.get(),
        "tenant_id": tenant_id_ctx.get(),
        "church_id": church_id_ctx.get(),
    }


def inc_counter(name: str, **labels: str) -> None:
    key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
    with _METRICS_LOCK:
        _COUNTERS[key] += 1


def observe_latency(name: str, latency_ms: float) -> None:
    with _METRICS_LOCK:
        bucket = _LATENCY[name]
        bucket["count"] += 1
        bucket["total_ms"] += float(latency_ms)
        bucket["max_ms"] = max(float(bucket["max_ms"]), float(latency_ms))


def time_workflow(metric_name: str):
    start = time.perf_counter()

    class _Timer:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            elapsed_ms = (time.perf_counter() - start) * 1000
            observe_latency(metric_name, elapsed_ms)
            return False

    return _Timer()


def snapshot_metrics() -> dict:
    with _METRICS_LOCK:
        counters = [
            {
                "name": name,
                "labels": dict(labels),
                "value": value,
            }
            for (name, labels), value in sorted(_COUNTERS.items(), key=lambda x: (x[0][0], x[0][1]))
        ]
        latency = {}
        for name, stats in _LATENCY.items():
            count = int(stats["count"])
            total_ms = float(stats["total_ms"])
            max_ms = float(stats["max_ms"])
            latency[name] = {
                "count": count,
                "avg_ms": round(total_ms / count, 2) if count else 0.0,
                "max_ms": round(max_ms, 2),
            }
    return {"counters": counters, "latency": latency}


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = get_request_context()
        record.request_id = ctx["request_id"]
        record.tenant_id = ctx["tenant_id"]
        record.church_id = ctx["church_id"]
        return True
