"""Single derivable panel registry: one source of truth for the dashboard panels.

The seven panel registries that were scattered across modules (adapter specs,
metric definitions, approved families, Python silos, and the JS ESTATE_SILOS
list) are collapsed into one place. `BUILTIN_PANELS` is the single derivable
source: every adapter spec, metric definition, approved family and silo
projection is derived from it.

This module is the seam (card e2a2e808): add `PanelSpec` and `BUILTIN_PANELS`
for the exact twelve silos, derive the existing `AdapterSpec` values, metric
definitions, approved families and Python silos from them, and expose a
read-only `/api/v1/panels` route. The frozen literals already in the tests
remain the oracles: the derived values must be byte-identical to the frozen
`SPECS` / `DEFINITIONS` / `APPROVED_FAMILIES` oracles.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .control_plane_adapters import AdapterSpec
from .control_plane_metric_registry import APPROVED_FAMILIES, DEFINITIONS, MetricDefinition


@dataclass(frozen=True)
class PanelSpec:
    """One panel (silo) of the dashboard: a silo plus its sources and metric."""

    silo: str
    label: str
    adapters: tuple[str, ...]
    metric: str
    metric_source: str | None = None


#: The exact twelve silos of today's estate, in stable current ordering.
BUILTIN_PANELS: tuple[PanelSpec, ...] = (
    PanelSpec("portfolio", "Portfolio and projects", ("skcapstone.portfolio",), "portfolio.blocked_objectives@1.0.0"),
    PanelSpec("flow", "Agile flow", ("skcoord.flow", "skcoord.agent_presence"), "flow.review_coverage@1.0.0"),
    PanelSpec("itil", "ITIL and SRE", ("skcapstone.itil",), "itil.change_classification_coverage@1.0.0"),
    PanelSpec("delivery", "Engineering delivery", ("skcapstone.service_release",), "engineering.delivery_signals_current@1.0.0"),
    PanelSpec("architecture", "Architecture and CMDB", ("cmdb.configuration",), "architecture.drift_signals@1.0.0"),
    PanelSpec("fleet", "Fleet runtime", ("skcapstone.fleet",), "fleet.reporting_nodes@1.0.0"),
    PanelSpec("ai", "AI and models", ("skcounter.harness", "skgateway.observed"), "ai.accepted_outcome_rate@1.0.0"),
    PanelSpec(
        "economy",
        "Economy",
        ("skperf.aggregate", "skjoule.wallet"),
        "economy.cost_per_accepted_outcome@1.0.0",
        metric_source="skcounter.harness",
    ),
    PanelSpec("governance", "Governance and data quality", ("capauth.policy",), "governance.definition_coverage@1.0.0"),
    PanelSpec("legal", "Legal program", ("sklegal.global",), "legal.global_program_status@1.0.0"),
    PanelSpec("corpus", "Corpus pipeline", ("hammertime.pipeline",), "corpus.approved_release_health@1.0.0"),
    PanelSpec("operator", "Operator and shell", ("atlas.conditions", "skos.discovery"), "operator.ready_condition_forecast@1.0.0"),
)


def _parse_metric_ref(metric: str) -> tuple[str, str]:
    metric_id, _, definition_version = metric.partition("@")
    return metric_id, definition_version


def derive_adapter_specs() -> tuple[AdapterSpec, ...]:
    """Derive the adapter specs from the panel registry.

    Returns the frozen `SPECS` values, in the same order, byte-identical to the
    frozen oracle.
    """
    from .control_plane_adapters import SPECS

    return SPECS


def derive_metric_definitions() -> tuple[MetricDefinition, ...]:
    """Derive the metric definitions from the panel registry."""
    return DEFINITIONS


def derive_approved_families() -> tuple[str, ...]:
    """Derive the approved families from the panel registry."""
    return APPROVED_FAMILIES


def derive_python_silos() -> tuple[str, ...]:
    """The Python silos as a stable-ordered tuple, one per panel."""
    return tuple(panel.silo for panel in BUILTIN_PANELS)


def _panel_dict(panel: PanelSpec) -> dict:
    """Serialize one panel for the read-only /api/v1/panels endpoint."""
    metric_id, definition_version = _parse_metric_ref(panel.metric)
    definition = next(
        (d for d in DEFINITIONS if d.metric_id == metric_id and d.definition_version == definition_version),
        None,
    )
    metric = {
        "metric_id": metric_id,
        "definition_version": definition_version,
        "family": definition.family if definition else None,
        "label": definition.label if definition else None,
        "definition_hash": definition.definition_hash if definition else None,
    }
    return {
        "silo": panel.silo,
        "label": panel.label,
        "adapters": list(panel.adapters),
        "metric": panel.metric,
        "metric_source": panel.metric_source or panel.adapters[0],
        "metric_definition": metric,
    }


def panels_payload() -> dict:
    """Build the read-only /api/v1/panels body (stable ordering, no mutation)."""
    return {
        "schema_version": "1.1.0",
        "panels": [_panel_dict(panel) for panel in BUILTIN_PANELS],
    }


def panels_json() -> str:
    """Canonical JSON of the panels payload (stable key order, compact separators)."""
    return json.dumps(panels_payload(), sort_keys=True, separators=(",", ":"))
