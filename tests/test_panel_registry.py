"""Single-source panel registry tests (card e2a2e808).

Proves the derived values are byte-identical to the frozen oracles (AC-2, AC-3),
and that the read-only /api/v1/panels endpoint returns the exact twelve panels
in stable ordering (AC-4).
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from skdashboard.control_plane_adapters import SPECS
from skdashboard.control_plane_metric_registry import (
    APPROVED_FAMILIES,
    DEFINITIONS,
)
from skdashboard.dashboard import create_app
from skdashboard.panel_registry import (
    BUILTIN_PANELS,
    PanelSpec,
    derive_adapter_specs,
    derive_approved_families,
    derive_metric_definitions,
    derive_python_silos,
    panels_json,
    panels_payload,
)

READ_HEADERS = {
    "Authorization": "Bearer valid-read",
    "Origin": "https://10.0.0.139:7778",
}

FROZEN_SPECS_ORACLE = (
    # (adapter_id, owner, population, fields, ttl_seconds, timeout_ms, classification)
    (
        "skcapstone.portfolio",
        "SKCapstone",
        "portfolio_project_work",
        ("total", "open", "in_progress", "done"),
        60,
        1_000,
        "internal",
    ),
    (
        "skcoord.flow",
        "skcoord",
        "task_flow",
        ("open", "in_progress", "done", "blocked"),
        60,
        1_000,
        "internal",
    ),
    (
        "skcoord.agent_presence",
        "skcoord",
        "agent_presence",
        ("total_agents", "active_agents"),
        60,
        1_000,
        "internal",
    ),
    (
        "skcapstone.itil",
        "SKCapstone ITIL",
        "itil_records",
        ("open_incidents", "sev1", "sev2", "awaiting_cab"),
        60,
        1_000,
        "internal",
    ),
    (
        "skcapstone.service_release",
        "SKCapstone",
        "service_release_observations",
        ("services", "releases"),
        60,
        1_000,
        "internal",
    ),
    (
        "cmdb.configuration",
        "CMDB",
        "configuration_items",
        ("total", "operational", "degraded", "other_status", "fresh", "stale", "unknown"),
        60,
        1_000,
        "internal",
    ),
    (
        "skcapstone.fleet",
        "SKCapstone Fleet",
        "fleet_runtime",
        ("graded", "skipped", "error", "warn", "info", "ok"),
        60,
        1_000,
        "internal",
    ),
    (
        "skcounter.harness",
        "SKCounter",
        "harness_reported",
        (
            "tokens_total",
            "cost_usd",
            "cost_state",
            "observation_count",
            "fresh_collectors",
            "delayed_collectors",
            "stale_collectors",
        ),
        60,
        1_000,
        "internal",
    ),
    (
        "skgateway.observed",
        "SKGateway",
        "gateway_observed",
        (
            "tokens_total",
            "cost_usd",
            "cost_state",
            "observation_count",
            "fresh_collectors",
            "delayed_collectors",
            "stale_collectors",
        ),
        60,
        1_000,
        "internal",
    ),
    (
        "skperf.aggregate",
        "SKPerf",
        "approved_benchmarks",
        ("regressions", "capacity_pressure"),
        60,
        1_000,
        "internal",
    ),
    (
        "skjoule.wallet",
        "SKJoule",
        "wallets",
        ("total_supply", "active_agents"),
        60,
        1_000,
        "internal",
    ),
    (
        "capauth.policy",
        "CapAuth",
        "policy_health",
        ("available", "denials"),
        60,
        1_000,
        "confidential",
    ),
    (
        "atlas.conditions",
        "Atlas",
        "operator_conditions",
        ("open_conditions", "ready_actions"),
        60,
        1_000,
        "confidential",
    ),
    (
        "skos.discovery",
        "SKOS",
        "module_discovery",
        ("discovered", "unavailable"),
        60,
        1_000,
        "internal",
    ),
    (
        "sklegal.global",
        "SKLegal",
        "policy_filtered_global_aggregate",
        ("matters", "deadline_pressure"),
        60,
        1_000,
        "confidential",
    ),
    (
        "hammertime.pipeline",
        "HammerTime",
        "approved_aggregate_pipeline",
        ("approved_releases", "pipeline_failures"),
        60,
        1_000,
        "confidential",
    ),
)

FROZEN_DEFINITIONS_ORACLE = {
    "portfolio.blocked_objectives": (
        "portfolio",
        "Blocked objectives",
        "lower_is_better",
        "measured",
        "count",
        "SKCapstone",
        "skcapstone.portfolio",
    ),
    "flow.review_coverage": (
        "flow",
        "Review coverage",
        "higher_is_better",
        "derived",
        "ratio_percent",
        "skcoord",
        "skcoord.flow",
    ),
    "itil.change_classification_coverage": (
        "itil_sre",
        "Change classification coverage",
        "higher_is_better",
        "derived",
        "ratio_percent",
        "SKCapstone ITIL",
        "skcapstone.itil",
    ),
    "engineering.delivery_signals_current": (
        "delivery",
        "Current delivery signals",
        "higher_is_better",
        "measured",
        "count",
        "SKCapstone",
        "skcapstone.service_release",
    ),
    "architecture.drift_signals": (
        "architecture",
        "Material drift signals",
        "lower_is_better",
        "derived",
        "count",
        "CMDB",
        "cmdb.configuration",
    ),
    "fleet.reporting_nodes": (
        "fleet",
        "Reporting fleet nodes",
        "higher_is_better",
        "measured",
        "count",
        "SKCapstone Fleet",
        "skcapstone.fleet",
    ),
    "ai.accepted_outcome_rate": (
        "ai_models",
        "Accepted AI outcome rate",
        "higher_is_better",
        "derived",
        "ratio_percent",
        "SKCounter",
        "skcounter.harness",
    ),
    "ai.gateway_observation_count": (
        "ai_models",
        "Gateway observation count",
        "context_only",
        "measured",
        "count",
        "SKGateway",
        "skgateway.observed",
    ),
    "economy.cost_per_accepted_outcome": (
        "economy",
        "Cost per accepted outcome",
        "lower_is_better",
        "estimated",
        "ratio",
        "SKCounter",
        "skcounter.harness",
    ),
    "governance.definition_coverage": (
        "governance",
        "Metric definition coverage",
        "higher_is_better",
        "derived",
        "ratio_percent",
        "CapAuth",
        "capauth.policy",
    ),
    "legal.global_program_status": (
        "legal",
        "Policy-filtered legal program status",
        "context_only",
        "measured",
        "count",
        "SKLegal",
        "sklegal.global",
    ),
    "corpus.approved_release_health": (
        "corpus_pipeline",
        "Approved corpus release health",
        "higher_is_better",
        "measured",
        "count",
        "HammerTime",
        "hammertime.pipeline",
    ),
    "operator.ready_condition_forecast": (
        "operator_shell",
        "Ready condition forecast",
        "context_only",
        "forecast",
        "count",
        "Atlas",
        "atlas.conditions",
    ),
}

FROZEN_FAMILIES_ORACLE = (
    "portfolio",
    "flow",
    "itil_sre",
    "delivery",
    "architecture",
    "fleet",
    "ai_models",
    "economy",
    "governance",
    "legal",
    "corpus_pipeline",
    "operator_shell",
)


def _read_app():
    return create_app(
        Path("/tmp/does-not-matter"),
        control_plane_authorizer=lambda bearer, capability, _target: (
            bearer == "valid-read" and capability == "skdashboard.read"
        ),
    )


def test_builtin_panels_are_the_exact_twelve_silos() -> None:
    assert len(BUILTIN_PANELS) == 12
    assert [p.silo for p in BUILTIN_PANELS] == [
        "portfolio",
        "flow",
        "itil",
        "delivery",
        "architecture",
        "fleet",
        "ai",
        "economy",
        "governance",
        "legal",
        "corpus",
        "operator",
    ]
    for panel in BUILTIN_PANELS:
        assert isinstance(panel, PanelSpec)
        assert len(panel.adapters) >= 1
        assert "@" in panel.metric
        assert panel.signal


def test_legacy_registries_are_consumers_not_parallel_literals() -> None:
    root = Path(__file__).parents[1] / "src" / "skdashboard"
    assert "AdapterSpec(" not in (root / "control_plane_adapters.py").read_text()
    metric_source = (root / "control_plane_metric_registry.py").read_text()
    assert "MetricDefinition(" not in metric_source
    assert "APPROVED_FAMILIES = (" not in metric_source
    assert '"portfolio",\n        "flow"' not in (root / "control_plane_scope.py").read_text()


def test_derived_adapter_specs_are_byte_identical_to_frozen_oracle() -> None:
    derived = derive_adapter_specs()
    assert len(derived) == 16
    for spec, oracle in zip(derived, FROZEN_SPECS_ORACLE):
        assert (
            spec.adapter_id,
            spec.owner,
            spec.population,
            spec.fields,
            spec.ttl_seconds,
            spec.timeout_ms,
            spec.classification,
        ) == oracle
    assert derived == SPECS


def test_derived_metric_definitions_equal_frozen_oracle() -> None:
    definitions = derive_metric_definitions()
    by_id = {d.metric_id: d for d in definitions}
    assert set(by_id) == set(FROZEN_DEFINITIONS_ORACLE)
    for metric_id, (
        family,
        label,
        polarity,
        kind,
        method,
        owner,
        adapter_id,
    ) in FROZEN_DEFINITIONS_ORACLE.items():
        d = by_id[metric_id]
        assert d.family == family
        assert d.label == label
        assert d.polarity == polarity
        assert d.measurement_kind == kind
        assert d.method == method
        assert d.source_owner == owner
        assert d.adapter_id == adapter_id
    assert definitions == DEFINITIONS


def test_derived_families_and_silos_equal_frozen_oracles() -> None:
    assert derive_approved_families() == FROZEN_FAMILIES_ORACLE
    assert derive_approved_families() == APPROVED_FAMILIES
    silos = derive_python_silos()
    assert silos == (
        "portfolio",
        "flow",
        "itil",
        "delivery",
        "architecture",
        "fleet",
        "ai",
        "economy",
        "governance",
        "legal",
        "corpus",
        "operator",
    )
    # The twelve panels map 1:1 onto the twelve families via their metric family:
    # ai -> ai_models, itil -> itil_sre, corpus -> corpus_pipeline, operator -> operator_shell.
    assert len(derive_approved_families()) == len(silos) == 12


def test_panel_metrics_all_resolve_in_registry() -> None:
    registry = {d.metric_id for d in derive_metric_definitions()}
    for panel in BUILTIN_PANELS:
        metric_id, _, version = panel.metric.partition("@")
        assert metric_id in registry
        assert version == "1.0.0"


def test_panels_payload_is_stable_and_canonical() -> None:
    payload = panels_payload()
    assert len(payload["panels"]) == 12
    assert [p["silo"] for p in payload["panels"]] == [
        "portfolio",
        "flow",
        "itil",
        "delivery",
        "architecture",
        "fleet",
        "ai",
        "economy",
        "governance",
        "legal",
        "corpus",
        "operator",
    ]
    # Stable ordering: repeated calls yield identical canonical JSON.
    assert panels_json() == panels_json()
    assert all(panel["signal"] for panel in payload["panels"])
    legal = next(panel for panel in payload["panels"] if panel["silo"] == "legal")
    assert legal["unavailable_signal"] == "Policy-filtered aggregate unavailable"
    for panel in payload["panels"]:
        definition = panel["metric_definition"]
        registered = next(
            item
            for item in DEFINITIONS
            if item.metric_id == definition["metric_id"]
            and item.definition_version == definition["definition_version"]
        )
        assert definition["definition_hash"] == registered.definition_hash


def test_api_v1_panels_returns_twelve_panels() -> None:
    client = TestClient(_read_app())
    response = client.get("/api/v1/panels")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.1.0"
    assert [p["silo"] for p in body["panels"]] == [
        "portfolio",
        "flow",
        "itil",
        "delivery",
        "architecture",
        "fleet",
        "ai",
        "economy",
        "governance",
        "legal",
        "corpus",
        "operator",
    ]
    # The honest unavailable sklegal.global tile: present, with its metric family "legal".
    legal = next(p for p in body["panels"] if p["silo"] == "legal")
    assert legal["metric_definition"]["family"] == "legal"
    assert legal["metric_definition"]["label"] == "Policy-filtered legal program status"


def test_api_v1_panels_etag_is_stable() -> None:
    client = TestClient(_read_app())
    first = client.get("/api/v1/panels")
    unchanged = client.get("/api/v1/panels", headers={"If-None-Match": first.headers["ETag"]})
    assert first.status_code == 200
    assert unchanged.status_code == 304


def test_panels_endpoint_is_read_only_and_requires_no_capability() -> None:
    client = TestClient(_read_app())
    # No bearer required: this is public read-only metadata.
    assert client.get("/api/v1/panels").status_code == 200
