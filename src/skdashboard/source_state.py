"""Independent NOW source health dimensions and deterministic rollups."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

DIMENSIONS = ("availability", "freshness", "coverage", "data_quality")
_STATES = {
    "availability": {"available", "unavailable", "unknown"},
    "freshness": {"fresh", "stale", "unknown"},
    "coverage": {"complete", "partial", "unknown"},
    "data_quality": {"valid", "degraded", "unknown"},
}


def source_state(
    *,
    availability: str = "unknown",
    freshness: str = "unknown",
    coverage: str = "unknown",
    data_quality: str = "unknown",
    required: bool = True,
    degradation_reasons: Iterable[Mapping[str, Any]] = (),
    **legacy: Any,
) -> dict[str, Any]:
    """Build a source state while retaining the legacy truth_state summary."""
    values = {"availability": availability, "freshness": freshness, "coverage": coverage, "data_quality": data_quality}
    for key, value in values.items():
        if value not in _STATES[key]:
            raise ValueError(f"invalid {key}: {value}")
    reasons = []
    for reason in degradation_reasons:
        if not isinstance(reason, Mapping) or not isinstance(reason.get("code"), str):
            raise ValueError("degradation reasons require a string code")
        reasons.append({"code": reason["code"], "message": str(reason.get("message", ""))[:500]})
    if legacy.get("policy_filtered"):
        reasons.append({"code": "POLICY_FILTERED", "message": "Source excluded by visibility policy"})
    if availability == "unavailable":
        truth = "unavailable"
    elif availability == "unknown" or freshness == "unknown" or coverage == "unknown" or data_quality == "unknown":
        truth = "unknown"
    elif coverage == "partial" or data_quality == "degraded":
        truth = "partial"
    elif freshness == "stale":
        truth = "stale"
    else:
        truth = "current"
    return {**values, "required": bool(required), "degradation_reasons": reasons, "truth_state": truth}


def rollup_source_states(sources: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Roll up dimensions independently; no lifecycle or links are treated as evidence."""
    items = list(sources)
    dimensions: dict[str, str] = {}
    for dimension in DIMENSIONS:
        states = [str(item.get(dimension, "unknown")) for item in items]
        if not states:
            dimensions[dimension] = "unknown"
        elif dimension == "availability":
            dimensions[dimension] = "available" if any(s == "available" for s in states) else ("unavailable" if all(s == "unavailable" for s in states) else "unknown")
        elif "partial" in states:
            dimensions[dimension] = "partial"
        elif "degraded" in states:
            dimensions[dimension] = "degraded"
        elif "stale" in states:
            dimensions[dimension] = "stale"
        elif "unknown" in states:
            dimensions[dimension] = "unknown"
        else:
            dimensions[dimension] = states[0]
    reasons = [reason for item in items for reason in item.get("degradation_reasons", [])]
    truth = "unavailable" if dimensions["availability"] == "unavailable" else ("partial" if dimensions["coverage"] == "partial" or dimensions["data_quality"] == "degraded" else ("stale" if dimensions["freshness"] == "stale" else ("unknown" if "unknown" in dimensions.values() else "current")))
    return {**dimensions, "truth_state": truth, "sources": len(items), "degradation_reasons": reasons}
