from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skdashboard.schedule_recommendations import (
    SCENARIO_KINDS,
    compare_scenarios,
    explain_schedule_risk,
    preview_reschedule,
)

BINDING = {
    "projection_id": "schedule-1",
    "projection_version": "projection-v7",
    "projection_hash": "sha256:" + "a" * 64,
}
NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _changes():
    return [
        {"kind": "scope_change", "item_id": "release", "before": 10, "after": 8},
        {"kind": "capacity_change", "item_id": "service", "before": 1, "after": 0.8},
        {"kind": "dependency_slip", "item_id": "upstream", "before": 0, "after": 7},
        {"kind": "milestone_move", "item_id": "milestone", "before": "2026-09-01", "after": "2026-09-08"},
        {"kind": "itil_window", "item_id": "change-window", "before": None, "after": "blackout"},
        {"kind": "architecture_sequence", "item_id": "migration", "before": 2, "after": 3},
    ]


def test_all_required_scenario_types_are_reproducible_and_no_write():
    baseline = {"critical_path": {"method": "deterministic_date", "target": "2026-09-01"}, "flow": {"method": "aggregate_throughput", "p50": 3, "p85": 5, "p95": 7}}
    first = compare_scenarios(projection_binding=BINDING, baseline=baseline, changes=_changes(), created_at=NOW)
    second = compare_scenarios(projection_binding=BINDING, baseline=baseline, changes=reversed(_changes()), created_at=NOW)

    assert first == second
    assert {item["kind"] for item in first["changes"]} == SCENARIO_KINDS
    assert first["method_separation"]["blended"] is False
    assert first["comparison"]["baseline"] == baseline
    assert first["comparison"]["scenario"] == baseline
    assert first["writes_owner_records"] is False
    assert first["individual_ranking_prohibited"] is True
    assert first["scenario_hash"] == first["reproducibility_key"]


def test_unknown_scenario_kind_fails_closed():
    with pytest.raises(ValueError, match="unsupported"):
        compare_scenarios(projection_binding=BINDING, baseline={}, changes=[{"kind": "execute", "item_id": "x"}], created_at=NOW)


def test_ai_explanation_contains_balanced_evidence_uncertainty_and_alternatives():
    insight = explain_schedule_risk(
        forecast_ref="forecast://flow/7",
        target_id="release-1",
        source_versions=("projection-v7", "history-v4"),
        support_evidence=("evidence://dependency/slip",),
        counter_evidence=("evidence://capacity/recovery",),
        uncertainty=("future throughput can vary",),
        affected_outcomes=("outcome://release",),
        alternatives=("reduce scope", "move milestone"),
        risk="P85 delivery exceeds milestone",
        explanation="Dependency delay increases the aggregate delivery range.",
        expected_impact="P85 increases by two canonical periods.",
        model_provenance="model://schedule-explainer/v1",
        policy_reference="policy://schedule/read-only",
    )

    assert insight["state"] == "available"
    assert insight["action"] == "none"
    assert insight["support_evidence"] and insight["counter_evidence"]
    assert insight["uncertainty"] and insight["alternatives"]
    assert insight["affected_outcomes"] and insight["expected_impact"]
    assert insight["writes_owner_records"] is False


def test_ai_abstains_when_balancing_evidence_is_insufficient():
    insight = explain_schedule_risk(
        forecast_ref="forecast://flow/7", target_id="release-1", source_versions=("projection-v7",),
        support_evidence=("evidence://one",), counter_evidence=(), uncertainty=(), affected_outcomes=(), alternatives=(),
        risk="late", explanation="maybe", expected_impact="unknown", model_provenance="model://one", policy_reference="policy://read",
    )

    assert insight["state"] == "abstained"
    assert insight["model_provenance"] is None
    assert insight["risk"] is None
    assert insight["support_evidence"] == []
    assert "insufficient" in insight["abstention_reason"]


def test_reschedule_preview_is_exact_typed_expiring_and_never_authorizes_dispatch():
    kwargs = {
        "projection_binding": BINDING,
        "scenario_id": "scn-forecast-one",
        "scenario_hash": "sha256:" + "b" * 64,
        "owner_system": "skcoord",
        "owner_operation": "schedule.change.v1",
        "changes": ({"item_id": "release", "field": "planned_target", "before": "2026-09-01", "after": "2026-09-08", "evidence_refs": ["evidence://forecast/p85"]},),
        "blast_radius": {"items": ["release", "downstream"], "outcomes": ["outcome://release"]},
        "policy_decision": {"state": "conditional", "reference": "policy://schedule/preview"},
        "expires_at": NOW + timedelta(minutes=30),
        "rollback": ("restore planned_target to 2026-09-01",),
        "required_approvals": ("project_owner", "change_authority"),
    }
    first = preview_reschedule(**kwargs)
    second = preview_reschedule(**kwargs)

    assert first == second
    assert first["owner_operation"] == "schedule.change.v1"
    assert first["changes"][0]["field"] == "planned_target"
    assert first["blast_radius"]["items"]
    assert first["policy_decision"]["state"] == "conditional"
    assert first["authorization"]["required_hash"] == first["preview_hash"]
    assert first["authorization"]["dispatch_authorized"] is False
    assert first["authorization"]["required_approvals"] == ["project_owner", "change_authority"]
    assert first["rollback"] and first["expires_at"].endswith("Z")
    assert first["non_executing"] is True and first["writes_owner_records"] is False


def test_preview_rejects_untyped_change_and_missing_approval():
    base = dict(
        projection_binding=BINDING, scenario_id="scn-forecast-one", scenario_hash="sha256:" + "b" * 64,
        owner_system="skcoord", owner_operation="schedule.change.v1", blast_radius={},
        policy_decision={"state": "deny", "reference": "policy://deny"}, expires_at=NOW,
        rollback=("restore",), required_approvals=("owner",),
    )
    with pytest.raises(ValueError, match="dates or dependencies"):
        preview_reschedule(**base, changes=({"item_id": "x", "field": "person_rank", "evidence_refs": ["e://1"]},))
    with pytest.raises(ValueError, match="approvals"):
        preview_reschedule(**{**base, "required_approvals": ()}, changes=({"item_id": "x", "field": "sequence", "evidence_refs": ["e://1"]},))
