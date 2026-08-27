"""Read-only schedule scenarios, explanations, and exact action previews.

These builders are deliberately pure. They return canonical hash-bound artifacts and
never call an owner system. Critical-path dates belong to the bound schedule
projection; probabilistic flow periods belong to the forecast artifact.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping, Sequence

SCENARIO_KINDS = frozenset(
    {
        "scope_change",
        "capacity_change",
        "dependency_slip",
        "milestone_move",
        "itil_window",
        "architecture_sequence",
    }
)
_CHANGE_FIELDS = {
    "scope_change": "scope",
    "capacity_change": "capacity",
    "dependency_slip": "dependency_lag",
    "milestone_move": "planned_target",
    "itil_window": "itil_window",
    "architecture_sequence": "architecture_sequence",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def compare_scenarios(
    *,
    projection_binding: Mapping[str, str],
    baseline: Mapping[str, object],
    changes: Sequence[Mapping[str, object]],
    created_at: datetime,
) -> dict:
    """Build a reproducible comparison without changing the baseline or owner data."""

    required = {"projection_id", "projection_version", "projection_hash"}
    if set(projection_binding) != required or not all(projection_binding.values()):
        raise ValueError("an exact projection binding is required")
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    normalized = []
    for change in changes:
        kind = change.get("kind")
        if kind not in SCENARIO_KINDS:
            raise ValueError("unsupported schedule scenario kind")
        if not isinstance(change.get("item_id"), str) or not change["item_id"]:
            raise ValueError("scenario item_id is required")
        normalized.append(
            {
                "kind": kind,
                "item_id": change["item_id"],
                "field": _CHANGE_FIELDS[kind],
                "before": change.get("before"),
                "after": change.get("after"),
                "assumption": str(change.get("assumption") or "No additional assumption supplied"),
            }
        )
    normalized.sort(key=lambda item: (item["kind"], item["item_id"], _canonical(item)))
    baseline_hash = _hash(baseline)
    scenario_view = {
        "source_baseline_hash": baseline_hash,
        "overrides": [
            {
                "kind": item["kind"],
                "item_id": item["item_id"],
                "field": item["field"],
                "value": item["after"],
            }
            for item in normalized
        ],
    }
    identity = {
        "source": dict(projection_binding),
        "baseline_hash": baseline_hash,
        "scenario": scenario_view,
        "changes": normalized,
    }
    digest = _hash(identity)
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "schedule_scenario_comparison",
        "scenario_id": "scn-" + digest.removeprefix("sha256:")[:24],
        "scenario_hash": digest,
        "source_projection": dict(projection_binding),
        "baseline_hash": baseline_hash,
        "method_separation": {
            "date_projection": "deterministic critical path from owner dates and dependencies",
            "flow_projection": "probabilistic aggregate throughput in canonical periods",
            "blended": False,
        },
        "changes": normalized,
        "comparison": {"baseline": dict(baseline), "scenario": scenario_view},
        "created_at": created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "immutable": True,
        "mode": "no_write",
        "writes_owner_records": False,
        "individual_ranking_prohibited": True,
        "reproducibility_key": digest,
    }


def explain_schedule_risk(
    *,
    forecast_ref: str,
    target_id: str,
    source_versions: Sequence[str],
    support_evidence: Sequence[str],
    counter_evidence: Sequence[str],
    uncertainty: Sequence[str],
    affected_outcomes: Sequence[str],
    alternatives: Sequence[str],
    risk: str | None,
    explanation: str | None,
    expected_impact: str | None,
    model_provenance: str | None,
    policy_reference: str,
    minimum_support: int = 1,
) -> dict:
    """Return an evidence-grounded recommendation or a typed abstention."""

    sources = list(source_versions)
    evidence = list(support_evidence)
    enough = (
        len(evidence) >= minimum_support
        and bool(counter_evidence)
        and bool(uncertainty)
        and bool(affected_outcomes)
        and bool(alternatives)
        and all((risk, explanation, expected_impact, model_provenance, forecast_ref, target_id, policy_reference))
        and bool(sources)
    )
    identity = {
        "forecast_ref": forecast_ref,
        "target_id": target_id,
        "sources": sources,
        "support": evidence,
        "counter": list(counter_evidence),
        "uncertainty": list(uncertainty),
    }
    common = {
        "schema_version": "1.0.0",
        "insight_id": "insight-" + _hash(identity).removeprefix("sha256:")[:24],
        "forecast_ref": forecast_ref,
        "target_id": target_id,
        "truth_state": "current" if enough else "unavailable",
        "policy_reference": policy_reference,
        "source_versions": sources or ["unavailable"],
        "engine_provenance": "skdashboard.schedule_recommendations@1.0.0",
        "reproducibility_key": _hash(identity),
        "action": "none",
        "writes_owner_records": False,
        "individual_ranking_prohibited": True,
    }
    if not enough:
        return {
            **common,
            "state": "abstained",
            "model_provenance": None,
            "risk": None,
            "explanation": None,
            "support_evidence": [],
            "counter_evidence": [],
            "uncertainty": [],
            "affected_outcomes": [],
            "alternatives": [],
            "expected_impact": None,
            "abstention_reason": "insufficient supporting, counter, uncertainty, outcome, or alternative evidence",
        }
    return {
        **common,
        "state": "available",
        "model_provenance": model_provenance,
        "risk": risk,
        "explanation": explanation,
        "support_evidence": evidence,
        "counter_evidence": list(counter_evidence),
        "uncertainty": list(uncertainty),
        "affected_outcomes": list(affected_outcomes),
        "alternatives": list(alternatives),
        "expected_impact": expected_impact,
    }


def preview_reschedule(
    *,
    projection_binding: Mapping[str, str],
    scenario_id: str,
    scenario_hash: str,
    owner_system: str,
    owner_operation: str,
    changes: Sequence[Mapping[str, object]],
    blast_radius: Mapping[str, object],
    policy_decision: Mapping[str, str],
    expires_at: datetime,
    rollback: Sequence[str],
    required_approvals: Sequence[str],
) -> dict:
    """Create an exact, deterministic, non-executing owner action preview."""

    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    required_binding = {"projection_id", "projection_version", "projection_hash"}
    if set(projection_binding) != required_binding or not all(projection_binding.values()):
        raise ValueError("an exact projection binding is required")
    for name, value in (("projection_hash", projection_binding["projection_hash"]), ("scenario_hash", scenario_hash)):
        digest = value.removeprefix("sha256:") if isinstance(value, str) else ""
        if not isinstance(value, str) or not value.startswith("sha256:") or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"{name} must use sha256:<64 lowercase hex>")
    if not scenario_id or not owner_system or not owner_operation or not changes or not rollback or not required_approvals:
        raise ValueError("typed operation, changes, rollback, and approvals are required")
    operation_changes = []
    for change in changes:
        if change.get("field") not in {"planned_start", "planned_target", "dependency_lag", "sequence"}:
            raise ValueError("reschedule changes must be dates or dependencies")
        if not change.get("item_id") or not change.get("evidence_refs"):
            raise ValueError("each change requires an item and evidence")
        operation_changes.append(dict(change))
    body = {
        "source_projection": dict(projection_binding),
        "scenario_id": scenario_id,
        "scenario_hash": scenario_hash,
        "owner_system": owner_system,
        "owner_operation": owner_operation,
        "changes": operation_changes,
        "blast_radius": dict(blast_radius),
        "policy_decision": dict(policy_decision),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "rollback": list(rollback),
        "required_approvals": list(required_approvals),
    }
    exact_hash = _hash(body)
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "owner_reschedule_action_preview",
        "preview_id": "rsp-" + exact_hash.removeprefix("sha256:")[:24],
        "preview_hash": exact_hash,
        "status": "ready",
        **body,
        "authorization": {
            "mode": "exact_hash",
            "required_hash": exact_hash,
            "required_approvals": list(required_approvals),
            "dispatch_authorized": False,
        },
        "non_executing": True,
        "writes_owner_records": False,
    }
