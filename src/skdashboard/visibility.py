"""Safe aggregate target and experiment visibility projections."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA = "skdashboard.visibility.v1"
ROLES = frozenset({"operator", "viewer", "auditor"})
VIEWS = frozenset(
    {
        "target_inventory",
        "trends",
        "experiments",
        "confidence",
        "guardrails",
        "bottlenecks",
        "comparisons",
        "regressions",
        "cleanup",
        "recovery",
    }
)
_FORBIDDEN = frozenset(
    {
        "secret",
        "prompt",
        "response",
        "matter",
        "inbox",
        "corpus",
        "token",
        "credential",
    }
)
_ROW_FIELDS = frozenset(
    {
        "target_id",
        "experiment_id",
        "metric",
        "value",
        "unit",
        "status",
        "baseline",
        "candidate",
        "delta",
        "confidence",
        "threshold",
        "sample_size",
        "missingness",
        "latency_ms",
        "queue_depth",
        "throughput",
        "host",
        "model",
        "route",
        "seat",
    }
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bounded_text(value: Any, field: str) -> str:
    text = str(value).strip()
    if not text or len(text) > 160:
        raise ValueError(f"{field} must be non-empty and at most 160 characters")
    return text


def _safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return only typed aggregate fields, never arbitrary source payload fields."""

    unknown = set(row) - _ROW_FIELDS
    if unknown:
        raise ValueError(f"visibility row has unknown fields: {sorted(unknown)}")
    if any(word in str(key).lower() for key in row for word in _FORBIDDEN):
        raise ValueError("visibility row contains a protected field")
    result: dict[str, Any] = {}
    for key, value in row.items():
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ValueError(f"visibility row field {key} must be scalar")
        if isinstance(value, str) and len(value) > 160:
            raise ValueError(f"visibility row field {key} is too long")
        result[str(key)] = value
    return result


def _view(
    kind: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    target_revision: str,
    cohort: str,
    evaluator_version: str,
    freshness: str = "unknown",
    missingness: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    if kind not in VIEWS:
        raise ValueError("unknown visibility view")
    safe_rows = [_safe_row(dict(row)) for row in rows]
    safe_missingness = dict(missingness or {})
    if any(
        not isinstance(key, str)
        or not key
        or len(key) > 80
        or not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        for key, value in safe_missingness.items()
    ):
        raise ValueError("missingness must contain bounded non-negative integer counts")
    metadata = {
        "kind": kind,
        "target_revision": _bounded_text(target_revision, "target_revision"),
        "cohort": _bounded_text(cohort, "cohort"),
        "freshness": _bounded_text(freshness, "freshness"),
        "missingness": safe_missingness,
        "sample_size": len(safe_rows),
        "evaluator_version": _bounded_text(evaluator_version, "evaluator_version"),
        "rows": safe_rows,
    }
    return {"schema": SCHEMA, **metadata, "evidence_hash": _hash(metadata)}


def authorize(role: str) -> None:
    if role not in ROLES:
        raise PermissionError("dashboard visibility requires an authorized read-only role")


def project(
    kind: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    role: str = "viewer",
    target_revision: str = "unknown",
    cohort: str = "unknown",
    evaluator_version: str = "unknown",
    freshness: str = "unknown",
    missingness: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    authorize(role)
    return _view(
        kind,
        rows,
        target_revision=target_revision,
        cohort=cohort,
        evaluator_version=evaluator_version,
        freshness=freshness,
        missingness=missingness,
    )
