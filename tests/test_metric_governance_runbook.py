from __future__ import annotations

import json
from pathlib import Path

from skdashboard.control_plane_metric_registry import APPROVED_FAMILIES

ROOT = Path(__file__).parents[1]
RUNBOOK = ROOT / "docs/governance/METRIC_GOVERNANCE_RUNBOOK.md"
REGISTER = ROOT / "docs/governance/metric-governance.v1.json"


def _register() -> dict:
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def test_owner_register_covers_every_metric_family_and_governed_product() -> None:
    register = _register()
    families = register["families"]
    assert {entry["id"] for entry in families} == set(APPROVED_FAMILIES)
    assert all(
        entry["accountable_owner"] and entry["data_owner"] and entry["review_cadence"]
        for entry in families
    )
    products = {entry["id"]: entry for entry in register["products"]}
    assert set(products) == {
        "production_reports",
        "probabilistic_forecasts",
        "ai_evaluations",
        "recommendation_outcomes",
        "ux_task_measures",
        "accessibility_regressions",
    }
    assert all(
        entry["accountable_owner"] and entry["review_owner"] and entry["review_cadence"]
        for entry in products.values()
    )


def test_change_gate_requires_versioned_approval_and_golden_fixtures() -> None:
    required = set(_register()["required_change_fields"])
    assert {
        "proposal_version",
        "definition",
        "target",
        "exclusions",
        "thresholds",
        "golden_fixture_paths",
        "fixture_hashes_before",
        "fixture_hashes_after",
        "data_owner_approval",
        "accountable_owner_approval",
        "independent_review",
    } <= required
    text = RUNBOOK.read_text(encoding="utf-8").lower()
    assert "supersedes_decision_id" in text
    assert "historical reports retain their original" in text


def test_outcome_review_is_balanced_and_prohibits_individual_ranking() -> None:
    register = _register()
    assert set(register["required_outcome_measures"]) == {
        "calibration",
        "verified_effect",
        "harmful_false_positives",
        "abstention_quality",
        "acceptance",
        "override",
        "rework",
        "cost_per_accepted_outcome",
        "user_task_success",
        "accessibility_regression",
    }
    assert set(register["prohibited_dimensions"]) == {
        "person_id",
        "user_id",
        "agent_id",
        "individual_productivity",
        "individual_rank",
    }
    text = RUNBOOK.read_text(encoding="utf-8").lower()
    assert "acceptance is a disposition, not proof of correctness" in text
    assert "do not convert a range to a promised date" in text


def test_runbook_defines_goodhart_retirement_append_only_truth_and_backlog() -> None:
    text = RUNBOOK.read_text(encoding="utf-8").lower()
    for phrase in (
        "goodhart controls and retirement triggers",
        "balancing measures",
        "no recorded decision use for three scheduled reviews",
        "create an object with a json serializer, never string concatenation",
        "parse every existing nonblank line before writing",
        "structural events",
        "remain separate from evidence events",
        "prioritized evidence-backed backlog",
        "closing an item requires a separate verified-effect event",
    ):
        assert phrase in text
