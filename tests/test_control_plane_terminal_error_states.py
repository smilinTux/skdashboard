from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_protected_workspaces_replace_all_loading_placeholders_on_terminal_error() -> None:
    contracts = {
        "schedule.js": (
            "schedule-status",
            "schedule-meta",
            "schedule-truth",
            "schedule-rows",
            "schedule-table-rows",
            "schedule-dependency-rows",
        ),
        "reliability.js": (
            "reliability-status",
            "reliability-summary",
            "reliability-metric-rows",
            "breach-count",
            "reliability-breach-rows",
            "reliability-lineage-rows",
            "reliability-kedb-rows",
        ),
        "architecture.js": (
            "architecture-status",
            "architecture-summary",
            "architecture-metric-rows",
            "architecture-exception-count",
            "architecture-exception-rows",
            "architecture-topology-count",
            "architecture-node-rows",
            "architecture-edge-rows",
        ),
        "governance.js": (
            "governance-status",
            "governance-summary",
            "finding-count",
            "finding-rows",
            "lineage-count",
            "lineage-rows",
            "source-count",
            "source-rows",
            "history-count",
            "history-rows",
        ),
        "reports.js": (
            "reports-status",
            "reports-summary",
            "snapshot-count",
            "snapshot-rows",
            "metric-count",
            "metric-rows",
            "comparison-state",
            "comparison-rows",
            "narrative-count",
            "narrative-rows",
        ),
    }

    for filename, required_ids in contracts.items():
        source = (ROOT / "src/skdashboard/static/js" / filename).read_text(encoding="utf-8")
        assert "function renderUnavailable(message)" in source
        assert "renderUnavailable(error.message)" in source
        unavailable = source.split("function renderUnavailable(message)", 1)[1].split("\n}\n", 1)[
            0
        ]
        for required_id in required_ids:
            assert f'getElementById("{required_id}")' in unavailable
