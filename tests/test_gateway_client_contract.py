from pathlib import Path

import skdashboard


def test_shared_gateway_client_owns_filters_identity_and_distinct_states() -> None:
    static = Path(skdashboard.__file__).parent / "static"
    client = (static / "js" / "gateway_client.js").read_text(encoding="utf-8")

    for value in ("start", "end", "model", "backend", "provider", "node", "client", "app", "rail", "scope"):
        assert f'"{value}"' in client
    for state in ("loading", "stale", "unavailable", "partial", "denied", "empty"):
        assert f'"{state}"' in client
    assert 'endpoint !== "timeseries" && endpoint !== "summary"' in client
    assert "gatewayEvidence(payload)" in client
    assert "Mismatched gateway snapshot identity" in client


def test_fleet_uses_shared_summary_contract_and_preserves_location_filters() -> None:
    static = Path(skdashboard.__file__).parent / "static"
    script = (static / "js" / "fleet.js").read_text(encoding="utf-8")

    assert 'from "./gateway_client.js"' in script
    assert "readGateway(gatewayFilters(location.search), undefined, \"summary\")" in script
    assert "snapshot.evidence?.watermark" in script


def test_all_gateway_rails_link_to_the_shared_evidence_contract() -> None:
    static = Path(skdashboard.__file__).parent / "static"
    economy = (static / "gateway_economy.html").read_text(encoding="utf-8")
    ai = (static / "js" / "ai.js").read_text(encoding="utf-8")
    reliability = (static / "js" / "reliability.js").read_text(encoding="utf-8")

    assert 'name="backend"' in economy
    assert 'from "./gateway_client.js"' in ai
    assert 'from "./gateway_client.js"' in reliability
    assert "ai-gateway-evidence" in ai
    assert "reliability-gateway-evidence" in reliability
