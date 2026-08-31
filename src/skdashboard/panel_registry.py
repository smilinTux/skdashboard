"""Single derivable source for the dashboard's twelve built-in panels."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class AdapterDefinition:
    adapter_id: str
    owner: str
    population: str
    fields: tuple[str, ...]
    ttl_seconds: int = 60
    timeout_ms: int = 1_000
    classification: str = "internal"


@dataclass(frozen=True)
class MetricDefinitionSpec:
    metric_id: str
    definition_version: str
    family: str
    label: str
    unit: str
    polarity: str
    measurement_kind: str
    method: str
    expression: str
    source_owner: str
    adapter_id: str
    classification: str = "internal"
    scope_dimensions: tuple[str, ...] = ("portfolio_id",)
    grain: str = "estate"
    target: dict | None = None
    calculation_inputs: tuple[str, ...] = (
        "numerator",
        "denominator",
        "sample_size",
        "exclusions",
    )


@dataclass(frozen=True)
class PanelSpec:
    """One UI panel and every registry declaration derived from it."""

    silo: str
    label: str
    adapter_specs: tuple[AdapterDefinition, ...]
    metric_definitions: tuple[MetricDefinitionSpec, ...]
    signal: str
    unavailable_signal: str = ""
    metric_source: str | None = None
    adapter_registry_order: tuple[int, ...] = ()

    @property
    def adapters(self) -> tuple[str, ...]:
        return tuple(spec.adapter_id for spec in self.adapter_specs)

    @property
    def metric(self) -> str:
        definition = self.metric_definitions[0]
        return f"{definition.metric_id}@{definition.definition_version}"


def _adapter(
    adapter_id: str,
    owner: str,
    population: str,
    fields: tuple[str, ...],
    *,
    classification: str = "internal",
) -> AdapterDefinition:
    return AdapterDefinition(adapter_id, owner, population, fields, classification=classification)


def _metric(
    metric_id: str,
    family: str,
    label: str,
    unit: str,
    polarity: str,
    measurement_kind: str,
    method: str,
    expression: str,
    source_owner: str,
    adapter_id: str,
    *,
    classification: str = "internal",
    scope_dimensions: tuple[str, ...] = ("portfolio_id",),
) -> MetricDefinitionSpec:
    return MetricDefinitionSpec(
        metric_id,
        "1.0.0",
        family,
        label,
        unit,
        polarity,
        measurement_kind,
        method,
        expression,
        source_owner,
        adapter_id,
        classification,
        scope_dimensions,
    )


BUILTIN_PANELS: tuple[PanelSpec, ...] = (
    PanelSpec(
        "portfolio",
        "Portfolio and projects",
        (
            _adapter(
                "skcapstone.portfolio",
                "SKCapstone",
                "portfolio_project_work",
                ("total", "open", "in_progress", "done"),
            ),
        ),
        (
            _metric(
                "portfolio.blocked_objectives",
                "portfolio",
                "Blocked objectives",
                "objectives",
                "lower_is_better",
                "measured",
                "count",
                "numerator",
                "SKCapstone",
                "skcapstone.portfolio",
            ),
        ),
        "{0.open} open, {0.in_progress} in progress, {0.done} done",
    ),
    PanelSpec(
        "flow",
        "Agile flow",
        (
            _adapter(
                "skcoord.flow", "skcoord", "task_flow", ("open", "in_progress", "done", "blocked")
            ),
            _adapter(
                "skcoord.agent_presence",
                "skcoord",
                "agent_presence",
                ("total_agents", "active_agents"),
            ),
        ),
        (
            _metric(
                "flow.review_coverage",
                "flow",
                "Review coverage",
                "percent",
                "higher_is_better",
                "derived",
                "ratio_percent",
                "100 * numerator / denominator",
                "skcoord",
                "skcoord.flow",
            ),
        ),
        "{0.blocked} blocked, {0.in_progress} in progress, {1.active_agents} active agents",
    ),
    PanelSpec(
        "itil",
        "ITIL and SRE",
        (
            _adapter(
                "skcapstone.itil",
                "SKCapstone ITIL",
                "itil_records",
                ("open_incidents", "sev1", "sev2", "awaiting_cab"),
            ),
        ),
        (
            _metric(
                "itil.change_classification_coverage",
                "itil_sre",
                "Change classification coverage",
                "percent",
                "higher_is_better",
                "derived",
                "ratio_percent",
                "100 * numerator / denominator",
                "SKCapstone ITIL",
                "skcapstone.itil",
            ),
        ),
        "{0.open_incidents} open incidents, SEV1 {0.sev1}, SEV2 {0.sev2}, {0.awaiting_cab} awaiting CAB",
    ),
    PanelSpec(
        "delivery",
        "Engineering delivery",
        (
            _adapter(
                "skcapstone.service_release",
                "SKCapstone",
                "service_release_observations",
                ("services", "releases"),
            ),
        ),
        (
            _metric(
                "engineering.delivery_signals_current",
                "delivery",
                "Current delivery signals",
                "signals",
                "higher_is_better",
                "measured",
                "count",
                "numerator",
                "SKCapstone",
                "skcapstone.service_release",
            ),
        ),
        "{0.services} services, {0.releases} release observations",
    ),
    PanelSpec(
        "architecture",
        "Architecture and CMDB",
        (
            _adapter(
                "cmdb.configuration",
                "CMDB",
                "configuration_items",
                ("total", "operational", "degraded", "other_status", "fresh", "stale", "unknown"),
            ),
        ),
        (
            _metric(
                "architecture.drift_signals",
                "architecture",
                "Material drift signals",
                "signals",
                "lower_is_better",
                "derived",
                "count",
                "numerator",
                "CMDB",
                "cmdb.configuration",
            ),
        ),
        "{0.total} CIs, {0.degraded} degraded, {0.stale} stale",
    ),
    PanelSpec(
        "fleet",
        "Fleet runtime",
        (
            _adapter(
                "skcapstone.fleet",
                "SKCapstone Fleet",
                "fleet_runtime",
                ("graded", "skipped", "error", "warn", "info", "ok"),
            ),
        ),
        (
            _metric(
                "fleet.reporting_nodes",
                "fleet",
                "Reporting fleet nodes",
                "nodes",
                "higher_is_better",
                "measured",
                "count",
                "numerator",
                "SKCapstone Fleet",
                "skcapstone.fleet",
            ),
        ),
        "{0.graded} graded, {0.error} errors, {0.warn} warnings",
    ),
    PanelSpec(
        "ai",
        "AI and models",
        (
            _adapter(
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
            ),
            _adapter(
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
            ),
        ),
        (
            _metric(
                "ai.accepted_outcome_rate",
                "ai_models",
                "Accepted AI outcome rate",
                "percent",
                "higher_is_better",
                "derived",
                "ratio_percent",
                "100 * numerator / denominator",
                "SKCounter",
                "skcounter.harness",
                scope_dimensions=("portfolio_id", "measurement_lane"),
            ),
            _metric(
                "ai.gateway_observation_count",
                "ai_models",
                "Gateway observation count",
                "observations",
                "context_only",
                "measured",
                "count",
                "numerator",
                "SKGateway",
                "skgateway.observed",
                scope_dimensions=("portfolio_id", "measurement_lane"),
            ),
        ),
        "Harness {0.observation_count} observations; gateway {1.observation_count} observations",
    ),
    PanelSpec(
        "economy",
        "Economy",
        (
            _adapter(
                "skperf.aggregate",
                "SKPerf",
                "approved_benchmarks",
                ("regressions", "capacity_pressure"),
            ),
            _adapter("skjoule.wallet", "SKJoule", "wallets", ("total_supply", "active_agents")),
        ),
        (
            _metric(
                "economy.cost_per_accepted_outcome",
                "economy",
                "Cost per accepted outcome",
                "usd",
                "lower_is_better",
                "estimated",
                "ratio",
                "numerator / denominator",
                "SKCounter",
                "skcounter.harness",
                scope_dimensions=("portfolio_id", "measurement_lane"),
            ),
        ),
        "{0.regressions} performance regressions; {1.total_supply} Joule supply",
        metric_source="skcounter.harness",
    ),
    PanelSpec(
        "governance",
        "Governance and data quality",
        (
            _adapter(
                "capauth.policy",
                "CapAuth",
                "policy_health",
                ("available", "denials"),
                classification="confidential",
            ),
        ),
        (
            _metric(
                "governance.definition_coverage",
                "governance",
                "Metric definition coverage",
                "percent",
                "higher_is_better",
                "derived",
                "ratio_percent",
                "100 * numerator / denominator",
                "CapAuth",
                "capauth.policy",
            ),
        ),
        "{0.denials} policy denials; policy evidence {0.available}",
    ),
    PanelSpec(
        "legal",
        "Legal program",
        (
            _adapter(
                "sklegal.global",
                "SKLegal",
                "policy_filtered_global_aggregate",
                ("matters", "deadline_pressure"),
                classification="confidential",
            ),
        ),
        (
            _metric(
                "legal.global_program_status",
                "legal",
                "Policy-filtered legal program status",
                "status",
                "context_only",
                "measured",
                "count",
                "numerator",
                "SKLegal",
                "sklegal.global",
                classification="confidential",
            ),
        ),
        "{0.matters} matter-free aggregate records; deadline pressure {0.deadline_pressure}",
        unavailable_signal="Policy-filtered aggregate unavailable",
        adapter_registry_order=(14,),
    ),
    PanelSpec(
        "corpus",
        "Corpus pipeline",
        (
            _adapter(
                "hammertime.pipeline",
                "HammerTime",
                "approved_aggregate_pipeline",
                ("approved_releases", "pipeline_failures"),
                classification="confidential",
            ),
        ),
        (
            _metric(
                "corpus.approved_release_health",
                "corpus_pipeline",
                "Approved corpus release health",
                "releases",
                "higher_is_better",
                "measured",
                "count",
                "numerator",
                "HammerTime",
                "hammertime.pipeline",
                classification="confidential",
            ),
        ),
        "{0.approved_releases} approved releases; {0.pipeline_failures} pipeline failures",
        adapter_registry_order=(15,),
    ),
    PanelSpec(
        "operator",
        "Operator and shell",
        (
            _adapter(
                "atlas.conditions",
                "Atlas",
                "operator_conditions",
                ("open_conditions", "ready_actions"),
                classification="confidential",
            ),
            _adapter("skos.discovery", "SKOS", "module_discovery", ("discovered", "unavailable")),
        ),
        (
            _metric(
                "operator.ready_condition_forecast",
                "operator_shell",
                "Ready condition forecast",
                "conditions",
                "context_only",
                "forecast",
                "count",
                "numerator",
                "Atlas",
                "atlas.conditions",
                classification="confidential",
            ),
        ),
        "{0.open_conditions} open conditions, {0.ready_actions} ready-action observations; {1.discovered} SKOS modules",
        adapter_registry_order=(12, 13),
    ),
)


def derive_adapter_specs() -> tuple[Any, ...]:
    """Build the canonical adapter objects from ``BUILTIN_PANELS``."""
    from .control_plane_adapters import AdapterSpec

    ordered = []
    ordinal = 0
    for panel in BUILTIN_PANELS:
        order = panel.adapter_registry_order or tuple(
            range(ordinal, ordinal + len(panel.adapter_specs))
        )
        ordered.extend(zip(order, panel.adapter_specs))
        ordinal += len(panel.adapter_specs)
    return tuple(AdapterSpec(**asdict(spec)) for _order, spec in sorted(ordered))


def derive_metric_definitions() -> tuple[Any, ...]:
    """Build the canonical metric objects from ``BUILTIN_PANELS``."""
    from .control_plane_metric_registry import MetricDefinition

    return tuple(
        MetricDefinition(**asdict(definition))
        for panel in BUILTIN_PANELS
        for definition in panel.metric_definitions
    )


def derive_approved_families() -> tuple[str, ...]:
    return tuple(dict.fromkeys(panel.metric_definitions[0].family for panel in BUILTIN_PANELS))


def derive_python_silos() -> tuple[str, ...]:
    return tuple(panel.silo for panel in BUILTIN_PANELS)


def _definition_hash(definition: MetricDefinitionSpec) -> str:
    encoded = json.dumps(
        asdict(definition), allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _panel_dict(panel: PanelSpec) -> dict:
    definition = panel.metric_definitions[0]
    return {
        "silo": panel.silo,
        "label": panel.label,
        "adapters": list(panel.adapters),
        "metric": panel.metric,
        "metric_source": panel.metric_source or panel.adapters[0],
        "signal": panel.signal,
        "unavailable_signal": panel.unavailable_signal,
        "metric_definition": {
            "metric_id": definition.metric_id,
            "definition_version": definition.definition_version,
            "family": definition.family,
            "label": definition.label,
            "definition_hash": _definition_hash(definition),
        },
    }


def panels_payload() -> dict:
    return {"schema_version": "1.1.0", "panels": [_panel_dict(panel) for panel in BUILTIN_PANELS]}


def panels_json() -> str:
    return json.dumps(panels_payload(), sort_keys=True, separators=(",", ":"))
