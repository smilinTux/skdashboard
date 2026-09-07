"""Bounded, server-side projections of indexed SKCounter gateway observations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from starlette.responses import Response

SCHEMA = "skdashboard.gateway.v1"
MAX_WINDOW_SECONDS = 24 * 60 * 60
MAX_ROWS = 200
MAX_RESPONSE_BYTES = 256 * 1024
MAX_INDEX_BYTES = 512 * 1024
MAX_OBSERVATION_BYTES = 2 * 1024 * 1024
QUERY_TIMEOUT_SECONDS = 2.0
TTL_SECONDS = 180
ALLOWED_FILTERS = frozenset({"model", "provider", "node", "client", "app", "rail"})
ALLOWED_ROLES = frozenset({"operator", "viewer", "auditor"})


class GatewayQueryError(ValueError):
    """A safe, classified gateway query failure."""

    def __init__(self, reason: str, *, unavailable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.unavailable = unavailable


def _parse_time(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise GatewayQueryError(f"malformed_{name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise GatewayQueryError(f"malformed_{name}") from exc
    if parsed.tzinfo is None:
        raise GatewayQueryError(f"malformed_{name}")
    return parsed.astimezone(timezone.utc)


def parse_query(request) -> dict[str, Any]:
    unknown = set(request.query_params) - (
        {"start", "end", "limit", "role", "scope"} | ALLOWED_FILTERS
    )
    if unknown:
        raise GatewayQueryError("unsupported_filter")
    now = datetime.now(timezone.utc)
    end = _parse_time(request.query_params.get("end", now.isoformat()), "end")
    start = _parse_time(
        request.query_params.get(
            "start",
            datetime.fromtimestamp(end.timestamp() - 3600, timezone.utc).isoformat(),
        ),
        "start",
    )
    if end <= start or (end - start).total_seconds() > MAX_WINDOW_SECONDS:
        raise GatewayQueryError("window_out_of_bounds")
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError as exc:
        raise GatewayQueryError("malformed_limit") from exc
    if not 1 <= limit <= MAX_ROWS:
        raise GatewayQueryError("row_limit_out_of_bounds")
    granted_role = getattr(getattr(request, "state", None), "gateway_role", None)
    granted_scope = getattr(getattr(request, "state", None), "gateway_scope", None)
    if granted_role not in ALLOWED_ROLES:
        raise GatewayQueryError("unauthorized_role")
    requested_role = request.query_params.get("role", granted_role)
    if requested_role != granted_role:
        raise GatewayQueryError("unauthorized_role")
    if not isinstance(granted_scope, str):
        raise GatewayQueryError("unauthorized_scope")
    scope = request.query_params.get("scope", granted_scope)
    if scope != granted_scope:
        raise GatewayQueryError("unauthorized_scope")
    if (
        not scope
        or len(scope) > 128
        or any(
            ch
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for ch in scope
        )
    ):
        raise GatewayQueryError("malformed_scope")
    filters = {
        key: request.query_params[key]
        for key in ALLOWED_FILTERS
        if key in request.query_params
    }
    if any(not value or len(value) > 128 for value in filters.values()):
        raise GatewayQueryError("malformed_filter")
    return {
        "start": start,
        "end": end,
        "limit": limit,
        "role": granted_role,
        "scope": scope,
        "filters": filters,
    }


def _safe_read(path: Path, maximum: int) -> bytes:
    try:
        if not path.is_file() or path.stat().st_size > maximum:
            raise GatewayQueryError("source_unavailable", unavailable=True)
        return path.read_bytes()
    except OSError as exc:
        raise GatewayQueryError("source_unavailable", unavailable=True) from exc


def _default_provider(home: Path, query: dict[str, Any]) -> list[dict[str, Any]]:
    root = (home / "skcounter").resolve()
    index_path = root / "observation-index" / "latest.json"
    try:
        index = json.loads(_safe_read(index_path, MAX_INDEX_BYTES))
    except json.JSONDecodeError as exc:
        raise GatewayQueryError("malformed_index", unavailable=True) from exc
    entries = index.get("entries") if isinstance(index, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_ROWS:
        raise GatewayQueryError("malformed_index", unavailable=True)
    observations: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_path"), str):
            raise GatewayQueryError("malformed_index", unavailable=True)
        source = (root / entry["source_path"]).resolve()
        if root not in source.parents:
            raise GatewayQueryError("malformed_index", unavailable=True)
        raw = _safe_read(source, MAX_OBSERVATION_BYTES)
        expected = entry.get("sha256") or entry.get("source_sha256")
        if not isinstance(expected, str) or hashlib.sha256(raw).hexdigest() != expected:
            raise GatewayQueryError("observation_hash_mismatch", unavailable=True)
        try:
            observation = json.loads(raw)
        except json.JSONDecodeError:
            # A single damaged observation must not erase otherwise usable
            # coverage. Keep a machine-readable marker so the projection can
            # distinguish partial data from an empty result.
            observations.append({"_gateway_malformed": True})
            continue
        if not isinstance(observation, dict):
            observations.append({"_gateway_malformed": True})
            continue
        if observation.get("measurement_lane") != "gateway_observed":
            continue
        observations.append(observation)
    return observations


def _unknown(reason: str) -> dict[str, str]:
    return {"state": "unknown", "reason": reason}


def _facts_are_well_formed(facts: Any) -> bool:
    """Validate only the nested shapes consumed by the projection."""
    if not isinstance(facts, dict):
        return False
    breakdowns = facts.get("breakdowns", {})
    if not isinstance(breakdowns, dict) or any(
        not isinstance(values, list)
        or any(not isinstance(value, str) for value in values)
        for values in breakdowns.values()
    ):
        return False
    daily = facts.get("daily_token_rows", [])
    if not isinstance(daily, list) or any(
        not isinstance(row, dict)
        or (row.get("model") is not None and not isinstance(row["model"], str))
        or (row.get("backend") is not None and not isinstance(row["backend"], str))
        for row in daily
    ):
        return False
    gateway = facts.get("gateway", {})
    if not isinstance(gateway, dict) or not isinstance(
        gateway.get("backend_health", {}), dict
    ):
        return False
    return all(
        isinstance(facts.get(key, {}), dict)
        for key in (
            "latency_ms",
            "catalog",
            "buckets",
            "claim_health",
            "capacity",
            "queue",
            "errors_by_model",
        )
    ) and all(isinstance(key, str) for key in facts.get("catalog", {}))


def _per_model_snapshot(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Join the safe gateway facts into one truthful row per observed model."""
    breakdowns = facts.get("breakdowns", {})
    daily = facts.get("daily_token_rows", [])
    health = facts.get("gateway", {}).get("backend_health", {})
    latency = facts.get("latency_ms", {})
    catalog = facts.get("catalog", {})
    buckets = facts.get("buckets", {})
    claims = facts.get("claim_health", {})
    capacity = facts.get("capacity", {})
    queue = facts.get("queue", {})

    names = set(breakdowns.get("models", [])) if isinstance(breakdowns, dict) else set()
    if isinstance(daily, list):
        names.update(
            row.get("model")
            for row in daily
            if isinstance(row, dict) and row.get("model")
        )
    if isinstance(catalog, dict):
        names.update(catalog)

    rows = []
    for model in sorted(names):
        model_daily = [
            row for row in daily if isinstance(row, dict) and row.get("model") == model
        ]
        backends = sorted({row["backend"] for row in model_daily if row.get("backend")})
        model_latency = {
            key: value
            for key, value in latency.items()
            if isinstance(latency, dict) and (key == model or key.endswith(f"/{model}"))
        }
        rows.append(
            {
                "model": model,
                "catalog": (
                    catalog.get(model, _unknown("catalog_not_observed"))
                    if isinstance(catalog, dict)
                    else _unknown("catalog_not_observed")
                ),
                "backends": [
                    {
                        "backend": backend,
                        "health": (
                            health.get(backend, _unknown("backend_health_not_observed"))
                            if isinstance(health, dict)
                            else _unknown("backend_health_not_observed")
                        ),
                    }
                    for backend in backends
                ],
                "claim_health": (
                    claims.get(model, _unknown("claim_health_not_observed"))
                    if isinstance(claims, dict)
                    else _unknown("claim_health_not_observed")
                ),
                "buckets": (
                    buckets.get(model, _unknown("bucket_membership_not_observed"))
                    if isinstance(buckets, dict)
                    else _unknown("bucket_membership_not_observed")
                ),
                "capacity": (
                    capacity.get(model, _unknown("capacity_not_observed"))
                    if isinstance(capacity, dict)
                    else _unknown("capacity_not_observed")
                ),
                "queue": (
                    queue.get(model, queue)
                    if isinstance(queue, dict)
                    else _unknown("queue_not_observed")
                ),
                "latency_ms": model_latency or _unknown("model_latency_not_observed"),
                "errors": (
                    facts.get("errors_by_model", {}).get(
                        model, _unknown("model_error_counts_not_observed")
                    )
                    if isinstance(facts.get("errors_by_model"), dict)
                    else _unknown("model_error_counts_not_observed")
                ),
                "daily_token_rows": model_daily,
            }
        )
    return rows


def _matches(observation: dict[str, Any], filters: dict[str, str]) -> bool:
    facts = observation.get("facts", {})
    breakdowns = facts.get("breakdowns", {}) if isinstance(facts, dict) else {}
    aliases = {
        "model": "models",
        "provider": "providers",
        "node": "nodes",
        "client": "clients",
        "app": "apps",
        "rail": "rails",
    }
    for key, expected in filters.items():
        values = (
            breakdowns.get(aliases[key], []) if isinstance(breakdowns, dict) else []
        )
        if expected not in values:
            return False
    return True


def project(
    observations: list[dict[str, Any]], query: dict[str, Any], *, timeseries: bool
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    selected: list[dict[str, Any]] = []
    malformed = 0
    for item in observations:
        if item.get("_gateway_malformed"):
            malformed += 1
            continue
        if not _facts_are_well_formed(item.get("facts")):
            malformed += 1
            continue
        try:
            observed = _parse_time(item.get("observed_at"), "observed_at")
        except GatewayQueryError:
            malformed += 1
            continue
        if query["start"] <= observed <= query["end"] and _matches(
            item, query["filters"]
        ):
            selected.append(item)
    selected.sort(key=lambda item: item["observed_at"], reverse=True)
    # Apply the caller's bound after filtering and ordering. This protects both
    # summary and timeseries responses even when the index contains many rows.
    selected = selected[: query["limit"]]
    latest = (
        _parse_time(selected[0]["observed_at"], "observed_at") if selected else None
    )
    age = max(0.0, (now - latest).total_seconds()) if latest else None
    if not observations:
        state, reason = "empty", "no_indexed_gateway_observations"
    elif malformed:
        # Preserve the distinction between a valid empty query and damaged
        # indexed input. Consumers must not turn malformed telemetry into zero.
        state, reason = "partial", "malformed_observations_omitted"
    elif not selected:
        state, reason = "empty", "no_observations_match_query"
    elif age is not None and age > TTL_SECONDS:
        state, reason = "stale", "watermark_exceeds_ttl"
    else:
        state, reason = "current", None
    coverage = {
        "returned": len(selected),
        "examined": len(observations),
        "malformed": malformed,
    }
    common = {
        "schema_version": SCHEMA,
        "state": state,
        "unavailable_reason": reason,
        "observed_at": selected[0]["observed_at"] if selected else None,
        "watermark": (
            (
                selected[0].get("watermark")
                or selected[0].get("payload_hash")
                or selected[0].get("source_sha256")
            )
            if selected
            else None
        ),
        "age_seconds": age,
        "ttl_seconds": TTL_SECONDS,
        "coverage": coverage,
        "scope": query["scope"],
        "filters": query["filters"],
    }
    if timeseries:
        common["items"] = [
            {
                "observed_at": item["observed_at"],
                "watermark": item.get("watermark")
                or item.get("payload_hash")
                or item.get("source_sha256"),
                "facts": item.get("facts"),
            }
            for item in reversed(selected)
        ]
    else:
        facts = selected[0].get("facts") if selected else None
        common["summary"] = facts
        common["models"] = _per_model_snapshot(facts) if isinstance(facts, dict) else []
    return common


def _response(request, payload: dict[str, Any], status_code: int = 200) -> Response:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        payload = {
            "schema_version": SCHEMA,
            "state": "unavailable",
            "unavailable_reason": "response_byte_limit_exceeded",
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        status_code = 503
    etag = '"' + hashlib.sha256(encoded).hexdigest() + '"'
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=5",
        "Vary": "Authorization",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        encoded, status_code=status_code, media_type="application/json", headers=headers
    )


_RATE: dict[str, list[float]] = {}
_RATE_LIMIT = 60
_RATE_WINDOW = 60.0


def _rate_limited(request) -> bool:
    key = request.client.host if getattr(request, "client", None) else "unknown"
    now = time.monotonic()
    recent = [stamp for stamp in _RATE.get(key, []) if now - stamp < _RATE_WINDOW]
    if len(recent) >= _RATE_LIMIT:
        _RATE[key] = recent
        return True
    recent.append(now)
    _RATE[key] = recent
    return False


def handlers(home: Path, provider: Callable | None = None):
    source = provider or _default_provider

    def make(timeseries: bool):
        async def handle(request):
            if _rate_limited(request):
                return _response(
                    request,
                    {
                        "schema_version": SCHEMA,
                        "state": "unavailable",
                        "unavailable_reason": "rate_limited",
                    },
                    429,
                )
            try:
                query = parse_query(request)
                observations = await asyncio.wait_for(
                    asyncio.to_thread(source, home, query),
                    timeout=QUERY_TIMEOUT_SECONDS,
                )
                if not isinstance(observations, list):
                    raise GatewayQueryError("malformed_provider", unavailable=True)
                return _response(
                    request, project(observations, query, timeseries=timeseries)
                )
            except asyncio.TimeoutError:
                return _response(
                    request,
                    {
                        "schema_version": SCHEMA,
                        "state": "unavailable",
                        "unavailable_reason": "query_timeout",
                    },
                    503,
                )
            except GatewayQueryError as exc:
                status = (
                    503
                    if exc.unavailable
                    else (
                        403
                        if exc.reason in {"unauthorized_role", "unauthorized_scope"}
                        else 400
                    )
                )
                return _response(
                    request,
                    {
                        "schema_version": SCHEMA,
                        "state": "unavailable",
                        "unavailable_reason": exc.reason,
                    },
                    status,
                )

        return handle

    return make(False), make(True)
