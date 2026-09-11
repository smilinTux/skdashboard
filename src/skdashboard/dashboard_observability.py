"""Bounded, read-only telemetry projection for SKGateway and vLLM."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

_METRIC = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+0-9.eE]+)$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')
_INTERESTING = {"num_requests_running", "num_requests_waiting", "kv_cache_usage_perc", "prefix_cache_queries_total", "prefix_cache_hits_total", "num_preemptions_total", "prompt_tokens_total", "prompt_tokens_cached_total", "generation_tokens_total", "request_success_total", "time_to_first_token_seconds", "e2e_request_latency_seconds"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fetch(url: str, *, timeout: float = 2.5) -> tuple[str | None, str | None]:
    if not url or not url.startswith(("http://", "https://")):
        return None, "source is not configured"
    try:
        with urlopen(Request(url, headers={"Accept": "application/json,text/plain"}), timeout=timeout) as response:
            return response.read(2_000_000).decode("utf-8", "replace"), None
    except Exception as exc:  # telemetry must never break the dashboard
        return None, f"source unavailable: {type(exc).__name__}"


def _vllm(text: str) -> dict:
    metrics: dict[str, list[dict]] = {}
    for line in text.splitlines():
        match = _METRIC.match(line.strip())
        if not match or match.group(1).split(":")[-1] not in _INTERESTING:
            continue
        try:
            value = float(match.group(3))
        except ValueError:
            continue
        name = match.group(1).split(":")[-1]
        metrics.setdefault(name, []).append({"labels": dict(_LABEL.findall(match.group(2) or "")), "value": value})
    models = sorted({item["labels"].get("model_name") for rows in metrics.values() for item in rows if item["labels"].get("model_name")})
    def total(name: str) -> float:
        return sum(item["value"] for item in metrics.get(name, []))
    def first(name: str):
        rows = metrics.get(name, [])
        return rows[0]["value"] if rows else None
    return {"source": "vllm", "truth_state": "current", "models": models, "summary": {"running": total("num_requests_running"), "queued": total("num_requests_waiting"), "kv_cache_usage_percent": first("kv_cache_usage_perc"), "prefix_cache_queries": total("prefix_cache_queries_total"), "prefix_cache_hits": total("prefix_cache_hits_total"), "preemptions": total("num_preemptions_total"), "prompt_tokens": total("prompt_tokens_total"), "cached_prompt_tokens": total("prompt_tokens_cached_total"), "generation_tokens": total("generation_tokens_total")}, "metrics": metrics}


def _gateway(text: str) -> dict:
    raw = json.loads(text)
    backends = raw.get("backends") if isinstance(raw.get("backends"), dict) else {}
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    return {"source": "skgateway", "truth_state": "current", "status": raw.get("status", "unknown"), "version": raw.get("version"), "backends": backends, "pool": raw.get("pool", {}), "summary": {key: metrics.get(key) for key in ("totalRequests", "activeRequests", "errorCount", "recentRequests5m", "recentErrors5m", "recentTokens5m", "totalInputTokens", "totalOutputTokens", "totalCostUsd", "unpricedRequests") if key in metrics}, "latency": metrics.get("latency", {}), "models": metrics.get("costByModel", {}), "agents": metrics.get("costByAgent", {})}


def collect_gateway() -> dict:
    """Collect only SKGateway so unrelated vLLM latency cannot hide it."""
    observed_at = _now()
    text, error = _fetch(
        os.environ.get("SKDASHBOARD_GATEWAY_STATUS_URL", "http://chiap01:18790/status"),
        timeout=0.75,
    )
    if error:
        return {"observed_at": observed_at, "source": None, "errors": [f"skgateway: {error}"]}
    try:
        return {"observed_at": observed_at, "source": _gateway(text or ""), "errors": []}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {
            "observed_at": observed_at,
            "source": None,
            "errors": ["skgateway: invalid status response"],
        }


def _fleet(home: Path) -> dict:
    """Return a bounded worker projection with no free-form agent content."""
    from .dashboard_fleet import collect_workers

    raw = collect_workers(home)
    workers = []
    for item in raw.get("workers", [])[:256]:
        if not isinstance(item, dict):
            continue
        truth = item.get("truth_state")
        if truth not in {"current", "stale", "unavailable"}:
            truth = "unavailable"
        workers.append(
            {
                "name": str(item.get("name") or "unknown")[:128],
                "host": str(item.get("host") or "unknown")[:128],
                "state": str(item.get("state") or "unknown")[:32],
                "has_current_task": bool(item.get("task_id")),
                "last_seen": item.get("observed_at")
                if isinstance(item.get("observed_at"), str)
                else None,
                "age_seconds": item.get("age_seconds")
                if isinstance(item.get("age_seconds"), int)
                else None,
                "truth_state": truth,
            }
        )
    running = sum(item["truth_state"] == "current" for item in workers)
    stale = sum(item["truth_state"] == "stale" for item in workers)
    unavailable = len(workers) - running - stale
    return {
        "source": "skcapstone_fleet",
        "truth_state": "partial" if raw.get("errors") or unavailable else "current",
        "workers": workers,
        "summary": {
            "running": running,
            "stale": stale,
            "unavailable": unavailable,
            "total": len(workers),
            "truncated": len(raw.get("workers", [])) > len(workers),
        },
        "errors": ["worker projection unavailable"] if raw.get("errors") else [],
    }


def collect(home: Path | None = None) -> dict:
    errors: list[str] = []
    sources: list[dict] = []
    vllm_text, error = _fetch(os.environ.get("SKDASHBOARD_VLLM_METRICS", "http://127.0.0.1:11439/metrics"))
    if vllm_text:
        sources.append(_vllm(vllm_text))
    elif error:
        errors.append(f"vllm: {error}")
    gateway = collect_gateway()
    if gateway["source"]:
        sources.append(gateway["source"])
    errors.extend(gateway["errors"])
    if home is not None and not os.environ.get("SKDASHBOARD_FLEET_METRICS_ENDPOINT"):
        fleet = _fleet(Path(home))
        sources.append(fleet)
        errors.extend(f"fleet: {error}" for error in fleet["errors"])
    return {"schema_version": "1.0.0", "observed_at": _now(), "projected_at": _now(), "freshness": {"truth_state": "current" if sources and not errors else ("partial" if sources else "unavailable"), "age_seconds": 0, "ttl_seconds": 15}, "sources": sources, "errors": errors[:16]}
