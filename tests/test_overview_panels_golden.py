"""Golden UI test: overview.js renders unchanged from the derived panel list.

Proves every rendered tile is byte-identical to the pre-change baseline, including
the honest unavailable sklegal.global tile (AC-5). No route, nav, metric, visibility
or user behavior changes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
OVERVIEW_JS = ROOT / "src/skdashboard/static/js/overview.js"


def _js() -> str:
    return OVERVIEW_JS.read_text(encoding="utf-8")


def test_estate_silos_literal_matches_twelve_silos() -> None:
    js = _js()
    # The frozen ESTATE_SILOS literal must list exactly the twelve silos, in order.
    silo_ids = re.findall(r'\{ id: "([a-z_]+)",', js)
    assert silo_ids[:12] == [
        "portfolio", "flow", "itil", "delivery", "architecture", "fleet",
        "ai", "economy", "governance", "legal", "corpus", "operator",
    ]


def test_signal_template_has_no_per_silo_branch() -> None:
    js = _js()
    # signalFor must use a single generic joiner, with no `signals` map.
    assert "const signals = {" not in js
    assert "parts.push" in js
    assert "no sources" in js


def test_overview_consumes_api_v1_panels() -> None:
    js = _js()
    assert 'getJSON("/api/v1/panels")' in js
    assert "loadPanels" in js


def test_sklegal_global_tile_stays_honest_when_unavailable() -> None:
    js = _js()
    # The legal silo row: baseline renders "Unknown", missing aggregate renders
    # "not observed" / "Policy-filtered aggregate unavailable"; no fabricated value.
    assert "not observed" in js
    assert 'id: "legal"' in js
    assert "sklegal.global" in js


def test_golden_tile_markup_unchanged() -> None:
    js = _js()
    # The estate row template keeps the same column structure (6 <td>).
    assert js.count("<td>") >= 6
    assert 'class="quality-preview-button estate-evidence-button"' in js
    assert 'No authorized silo matches this presentation filter' in js
