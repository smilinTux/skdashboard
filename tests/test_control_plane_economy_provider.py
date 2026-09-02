from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from skdashboard.dashboard import create_app
from skdashboard.dashboard_economy_provider import (
    EconomyProjectionProvider,
    get_economy_projection,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _authorizer(bearer: str, capability: str, _target: str) -> bool:
    return (bearer, capability) in {("valid-read", "skdashboard.read")}


def _app(home: Path):
    return create_app(
        home,
        control_plane_authorizer=_authorizer,
        control_plane_economy_provider=EconomyProjectionProvider(),
    )


def _read_headers():
    return {"Authorization": "Bearer valid-read", "Origin": "https://10.0.0.139:7778"}


def _usage(lane: str) -> dict:
    return {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "selected_lane": lane,
        "available_lanes": [lane],
        "summary": {
            "input": 10,
            "output": 20,
            "cache_read": 0,
            "cache_write": 0,
            "reasoning": 0,
            "total": 30,
            "cost_usd": 1.25,
            "cost_state": "available",
        },
        "collectors": [],
        "coverage": {"expected_nodes": 1, "reporting_nodes": 1},
        "errors": [],
    }


@dataclass
class NetworkStatsFake:
    agent_balances: dict
    active_agents: int
    total_minted: int
    total_spent: int
    total_transfers: int


def _stats() -> NetworkStatsFake:
    return NetworkStatsFake({"chef": 300}, 1, 300, 0, 0)


def _usage_side_effect():
    def _effect(home, filters):
        return _usage((filters or {}).get("lane", "harness_reported"))
    return _effect


def test_projection_renders_bounded_aggregates_with_freshness(tmp_path: Path) -> None:
    with patch(
        "skdashboard.dashboard_skcounter.get_ai_usage",
        side_effect=_usage_side_effect(),
    ), patch(
        "skcapstone.skjoule.JouleEngine.get_network_stats", return_value=_stats()
    ):
        projection = get_economy_projection(tmp_path, {"role": "operator"}, now=NOW)

    assert projection["schema_version"] == "economy-projection/v1"
    assert projection["freshness"]["truth_state"] == "current"
    assert projection["freshness"]["age_seconds"] == 0
    items = projection["items"]
    harness = next(i for i in items if i.get("measurement_lane") == "harness_reported")
    gateway = next(i for i in items if i.get("measurement_lane") == "gateway_observed")
    assert harness["tokens"]["total"] == 30
    assert harness["cost_usd"] == 1.25
    assert harness["cost_state"] == "available"
    assert gateway["tokens"]["total"] == 30
    joule = next(i for i in items if i.get("source") == "skjoule.wallet")
    assert joule["total_supply"] == 300
    assert joule["active_agents"] == 1
    assert joule["has_observations"] is True


def test_missing_sources_fail_closed_not_zero(tmp_path: Path) -> None:
    with patch(
        "skdashboard.dashboard_skcounter.get_ai_usage",
        side_effect=RuntimeError("collector down"),
    ), patch(
        "skcapstone.skjoule.JouleEngine.get_network_stats",
        side_effect=ImportError("no skcapstone"),
    ):
        projection = get_economy_projection(tmp_path, None, now=NOW)

    assert projection["freshness"]["truth_state"] == "unavailable"
    for item in projection["items"]:
        if "measurement_lane" in item:
            assert item["tokens"] is None
            assert item["cost_usd"] is None
            assert item["truth_state"] == "unavailable"
        elif item.get("source") == "skjoule.wallet":
            assert item["total_supply"] is None
            assert item["active_agents"] is None
            assert item["truth_state"] == "unavailable"
    assert len(projection["errors"]) == 3


def test_stale_source_is_stale_not_current(tmp_path: Path) -> None:
    stale_time = (NOW - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    usage = _usage("harness_reported")
    usage["generated_at"] = stale_time
    with patch(
        "skdashboard.dashboard_skcounter.get_ai_usage", return_value=usage
    ), patch("skcapstone.skjoule.JouleEngine.get_network_stats", return_value=_stats()):
        projection = get_economy_projection(tmp_path, None, now=NOW)

    # 25 hours old exceeds the delayed threshold, so the projection must
    # report stale rather than current or zero-rendered aggregates.
    assert projection["freshness"]["truth_state"] == "stale"


def test_api_route_uses_governed_provider(tmp_path: Path) -> None:
    with patch(
        "skdashboard.dashboard_skcounter.get_ai_usage",
        side_effect=_usage_side_effect(),
    ), patch("skcapstone.skjoule.JouleEngine.get_network_stats", return_value=_stats()):
        client = TestClient(_app(tmp_path))
        response = client.get("/api/v1/economy/summary", headers=_read_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "economy-projection/v1"
        assert body["freshness"]["truth_state"] == "current"
        lanes = [i for i in body["items"] if "measurement_lane" in i]
        assert len(lanes) == 2
        assert any(i.get("source") == "skjoule.wallet" for i in body["items"])


def test_api_route_without_provider_fails_closed(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, control_plane_authorizer=_authorizer))
    response = client.get("/api/v1/economy/summary", headers=_read_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["freshness"]["truth_state"] == "unavailable"
    assert body["errors"][0]["code"] == "ECONOMY_UNAVAILABLE"


def test_invalid_lane_and_time_range_are_rejected(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    assert (
        client.get("/api/v1/economy/summary?measurement_lane=bogus", headers=_read_headers()).status_code
        == 400
    )
    assert (
        client.get("/api/v1/economy/summary?from=2026-08-01", headers=_read_headers()).status_code
        == 400
    )


def test_provider_currentness_checks_surround_the_owner_read(tmp_path: Path) -> None:
    calls = []

    class _State:
        def __init__(self, value: str):
            self.value = value

    class Verifier:
        def check_before_owner_read(self, _context):
            calls.append("before")
            return _State("allow")

        def check_after_owner_read(self, _context):
            calls.append("after")
            return _State("allow")

    class Query:
        role = "operator"
        scope = "estate"
        window = "latest"
        baseline = "none"
        service = "all"
        measurement_lane = "harness_reported"

    with patch("skdashboard.dashboard_skcounter.get_ai_usage", side_effect=_usage_side_effect()), \
         patch("skcapstone.skjoule.JouleEngine.get_network_stats", return_value=_stats()):
        provider = EconomyProjectionProvider()
        projection = provider.read(object(), Query(), tmp_path, currentness_verifier=Verifier())
    assert calls == ["before", "after"]
    assert projection["freshness"]["truth_state"] == "current"


def test_provider_denies_when_decision_not_current(tmp_path: Path) -> None:
    class _State:
        def __init__(self, value: str):
            self.value = value

    class Verifier:
        def check_before_owner_read(self, _context):
            return _State("deny")

        def check_after_owner_read(self, _context):
            return _State("deny")

    class Query:
        role = "operator"
        scope = "estate"
        window = "latest"
        baseline = "none"
        service = "all"

    provider = EconomyProjectionProvider()
    import pytest
    with pytest.raises(PermissionError, match="not current"):
        provider.read(object(), Query(), tmp_path, currentness_verifier=Verifier())


def test_no_mutation_credentials_or_protected_payload_access(tmp_path: Path) -> None:
    """AC3: the governed provider only adds read-only access to bounded
    aggregates and never introduces credential, prompt, or protected
    payload access."""
    provider_src = (
        Path(__file__).parents[1] / "src/skdashboard/dashboard_economy_provider.py"
    ).read_text(encoding="utf-8")
    assert "dashboard_skcounter" in provider_src
    assert "JouleEngine" in provider_src
    assert "get_network_stats" in provider_src
    # Only import/attribute-style access: no file I/O, no HTTP POST, no
    # credential or password handling in the provider module itself.
    lower = provider_src.lower()
    for forbidden in ("open(", "write(", "post(", "credential", "password"):
        assert forbidden not in lower


def test_response_is_canonical_json_and_etagged(tmp_path: Path) -> None:
    with patch("skdashboard.dashboard_skcounter.get_ai_usage", side_effect=_usage_side_effect()):
        client = TestClient(_app(tmp_path))
        response = client.get("/api/v1/economy/summary", headers=_read_headers())
    assert response.status_code == 200
    assert response.headers.get("etag")
    payload = json.loads(response.text)
    assert response.text == json.dumps(payload, sort_keys=True, separators=(",", ":"))
