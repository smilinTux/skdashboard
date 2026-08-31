"""Golden checks for metadata-driven overview panel rendering."""

from __future__ import annotations

from pathlib import Path

from skdashboard.panel_registry import panels_payload

ROOT = Path(__file__).parents[1]
OVERVIEW_JS = ROOT / "src/skdashboard/static/js/overview.js"


def _js() -> str:
    return OVERVIEW_JS.read_text(encoding="utf-8")


def test_estate_silos_has_no_parallel_literal() -> None:
    js = _js()
    assert "let ESTATE_SILOS = [];" in js
    assert '{ id: "portfolio"' not in js


def test_signal_template_has_no_per_silo_branch() -> None:
    js = _js()
    assert "const signals = {" not in js
    assert "silo.signal.replace" in js
    assert "aggregateValue(items[Number(source)], field)" in js


def test_overview_consumes_api_v1_panels_before_first_render() -> None:
    js = _js()
    assert 'getJSON("/api/v1/panels")' in js
    assert "await loadPanels();" in js
    assert "metric: p.metric" in js


def test_sklegal_global_tile_stays_honest_when_unavailable() -> None:
    legal = next(panel for panel in panels_payload()["panels"] if panel["silo"] == "legal")
    assert legal["adapters"] == ["sklegal.global"]
    assert legal["unavailable_signal"] == "Policy-filtered aggregate unavailable"
    assert "silo.unavailableSignal && !items[0]?.aggregate" in _js()


def test_all_signal_templates_preserve_the_frozen_tile_copy() -> None:
    assert [panel["signal"] for panel in panels_payload()["panels"]] == [
        "{0.open} open, {0.in_progress} in progress, {0.done} done",
        "{0.blocked} blocked, {0.in_progress} in progress, {1.active_agents} active agents",
        "{0.open_incidents} open incidents, SEV1 {0.sev1}, SEV2 {0.sev2}, {0.awaiting_cab} awaiting CAB",
        "{0.services} services, {0.releases} release observations",
        "{0.total} CIs, {0.degraded} degraded, {0.stale} stale",
        "{0.graded} graded, {0.error} errors, {0.warn} warnings",
        "Harness {0.observation_count} observations; gateway {1.observation_count} observations",
        "{0.regressions} performance regressions; {1.total_supply} Joule supply",
        "{0.denials} policy denials; policy evidence {0.available}",
        "{0.matters} matter-free aggregate records; deadline pressure {0.deadline_pressure}",
        "{0.approved_releases} approved releases; {0.pipeline_failures} pipeline failures",
        "{0.open_conditions} open conditions, {0.ready_actions} ready-action observations; {1.discovered} SKOS modules",
    ]


def test_golden_tile_markup_unchanged() -> None:
    js = _js()
    assert js.count("<td>") >= 6
    assert 'class="quality-preview-button estate-evidence-button"' in js
    assert "No authorized silo matches this presentation filter" in js
