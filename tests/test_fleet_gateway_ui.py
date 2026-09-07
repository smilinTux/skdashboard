from pathlib import Path

import skdashboard


def test_fleet_gateway_table_is_accessible_and_truthful() -> None:
    static = Path(skdashboard.__file__).parent / "static"
    html = (static / "fleet.html").read_text(encoding="utf-8")
    script = (static / "js" / "fleet.js").read_text(encoding="utf-8")

    assert 'aria-labelledby="gateway-nodes-title"' in html
    assert "<caption>Per-node gateway telemetry freshness and version truth</caption>" in script
    assert '<th scope="row">' in script
    assert 'from "./gateway_client.js"' in script
    assert 'readGateway(gatewayFilters(location.search), undefined, "summary")' in script
    assert "snapshot.node_totals" in script
    assert "No node is assumed healthy" in script
    assert "configuration_drift" in script
