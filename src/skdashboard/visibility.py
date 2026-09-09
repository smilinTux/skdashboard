"""Safe aggregate target and experiment visibility projections.

The projection deliberately accepts measurements, not source payloads.  This keeps
prompt, response, Matter, Inbox and corpus data outside the dashboard boundary.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SCHEMA = "skdashboard.visibility.v1"
ROLES = frozenset({"operator", "viewer", "auditor"})
_FORBIDDEN = frozenset({"secret", "prompt", "response", "matter", "inbox", "corpus", "token", "credential"})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _safe(value: Any, key: str = "") -> Any:
    """Allowlist scalar aggregate values and recursively remove sensitive fields."""
    lowered = key.lower()
    if any(word in lowered for word in _FORBIDDEN):
        return None
    if isinstance(value, Mapping):
        return {str(k): _safe(v, str(k)) for k, v in value.items() if not any(w in str(k).lower() for w in _FORBIDDEN)}
    if isinstance(value, (list, tuple)):
        return [_safe(v, key) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _view(kind: str, rows: Iterable[Mapping[str, Any]], *, target_revision: str, cohort: str,
          evaluator_version: str, freshness: str = "unknown", missingness: Mapping[str, int] | None = None) -> dict:
    safe_rows = [_safe(dict(row)) for row in rows]
    payload = {"kind": kind, "rows": safe_rows}
    return {
        "schema": SCHEMA, "kind": kind, "target_revision": target_revision,
        "cohort": cohort, "freshness": freshness,
        "missingness": dict(missingness or {}), "sample_size": len(safe_rows),
        "evaluator_version": evaluator_version, "evidence_hash": _hash(payload),
        "rows": safe_rows,
    }


def authorize(role: str) -> None:
    if role not in ROLES:
        raise PermissionError("dashboard visibility requires an authorized read-only role")


def project(kind: str, rows: Iterable[Mapping[str, Any]], *, role: str = "viewer",
            target_revision: str = "unknown", cohort: str = "unknown",
            evaluator_version: str = "unknown", freshness: str = "unknown",
            missingness: Mapping[str, int] | None = None) -> dict:
    authorize(role)
    return _view(kind, rows, target_revision=target_revision, cohort=cohort,
                 evaluator_version=evaluator_version, freshness=freshness,
                 missingness=missingness)


def target_inventory(rows, **kwargs): return project("target_inventory", rows, **kwargs)
def trends(rows, **kwargs): return project("trends", rows, **kwargs)
def experiments(rows, **kwargs): return project("experiments", rows, **kwargs)
def confidence(rows, **kwargs): return project("confidence", rows, **kwargs)
def guardrails(rows, **kwargs): return project("guardrails", rows, **kwargs)
def bottlenecks(rows, **kwargs): return project("bottlenecks", rows, **kwargs)
def comparisons(rows, **kwargs): return project("comparisons", rows, **kwargs)
def regressions(rows, **kwargs): return project("regressions", rows, **kwargs)
def cleanup(rows, **kwargs): return project("cleanup", rows, **kwargs)
def recovery(rows, **kwargs): return project("recovery", rows, **kwargs)
