"""Canonical node coverage arithmetic shared by dashboard rails."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def node_coverage(expected_nodes: Iterable[str], states: Mapping[str, str]) -> dict:
    """Return one node population summary without inventing health."""
    expected = sorted(set(expected_nodes) | set(states))
    reporting = sorted(states)
    missing = sorted(set(expected) - set(reporting))
    return {
        "expected_nodes": len(expected),
        "reporting_nodes": len(reporting),
        "fresh_collectors": sum(
            state in {"fresh", "current"} for state in states.values()
        ),
        "delayed_collectors": sum(state == "delayed" for state in states.values()),
        "stale_collectors": sum(state == "stale" for state in states.values()),
        "missing_nodes": missing,
        "percent": round(len(reporting) / len(expected) * 100, 1) if expected else None,
    }
