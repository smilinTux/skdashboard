import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from skdashboard.gateway_api import _per_model_snapshot, parse_query, project
from skdashboard.node_coverage import node_coverage


def _request(query=None, *, role="viewer", scope="fleet"):
    state = SimpleNamespace()
    if role is not None:
        state.gateway_role = role
    if scope is not None:
        state.gateway_scope = scope
    return SimpleNamespace(query_params=query or {}, state=state)


def test_query_requires_authenticated_gateway_grants():
    for request in (
        _request({"role": "operator", "scope": "tenant-other"}, role=None, scope=None),
        _request({"role": "operator"}, role="viewer"),
        _request({"scope": "tenant-other"}, scope="fleet"),
    ):
        try:
            parse_query(request)
        except ValueError as exc:
            assert str(exc) in {"unauthorized_role", "unauthorized_scope"}
        else:
            raise AssertionError("caller supplied gateway grant was accepted")


def test_query_defaults_to_authenticated_gateway_grants():
    query = parse_query(_request(role="auditor", scope="tenant-one"))

    assert query["role"] == "auditor"
    assert query["scope"] == "tenant-one"


def test_per_model_snapshot_joins_observed_facts_and_marks_missing_unknown():
    facts = {
        "breakdowns": {"models": ["qwen3.8"]},
        "gateway": {"backend_health": {"chiap08-qwen38": {"status": "ok"}}},
        "latency_ms": {"chiap08-qwen38/qwen3.8": {"p95": 400}},
        "queue": {"wait_ms_percentiles": {"unavailable": "not_exposed"}},
        "daily_token_rows": [
            {"model": "qwen3.8", "backend": "chiap08-qwen38", "request_count": 10}
        ],
    }

    row = _per_model_snapshot(facts)[0]

    assert row["model"] == "qwen3.8"
    assert row["backends"] == [{"backend": "chiap08-qwen38", "health": {"status": "ok"}}]
    assert row["latency_ms"]["chiap08-qwen38/qwen3.8"]["p95"] == 400
    assert row["catalog"] == {"state": "unknown", "reason": "catalog_not_observed"}
    assert row["claim_health"] == {
        "state": "unknown",
        "reason": "claim_health_not_observed",
    }


def test_summary_preserves_freshness_and_never_fabricates_model_zeroes():
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    observation = {
        "observed_at": observed.isoformat(),
        "payload_hash": "a" * 64,
        "facts": {"breakdowns": {"models": ["codex"]}, "daily_token_rows": []},
    }
    query = parse_query(_request())

    result = project([observation], query, timeseries=False)

    assert result["state"] == "current"
    assert result["watermark"] == "a" * 64
    assert result["models"][0]["model"] == "codex"
    assert "request_count" not in json.dumps(result["models"][0])


def test_malformed_and_empty_states_are_distinct():
    query = parse_query(_request())

    assert project([], query, timeseries=False)["state"] == "empty"
    partial = project([{"_gateway_malformed": True}], query, timeseries=False)
    assert partial["state"] == "partial"
    assert partial["coverage"]["malformed"] == 1


def test_malformed_nested_gateway_facts_are_omitted_without_exception():
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    query = parse_query(_request())
    malformed = [
        {
            "observed_at": observed.isoformat(),
            "payload_hash": "a" * 64,
            "facts": {"breakdowns": {"models": [{"name": "codex"}]}},
        },
        {
            "observed_at": observed.isoformat(),
            "payload_hash": "b" * 64,
            "facts": {
                "breakdowns": {"models": ["codex"]},
                "gateway": [],
            },
        },
    ]

    result = project(malformed, query, timeseries=False)

    assert result["state"] == "partial"
    assert result["unavailable_reason"] == "malformed_observations_omitted"
    assert result["coverage"] == {"returned": 0, "examined": 2, "malformed": 2}
    assert result["models"] == []


def test_non_string_observed_at_values_are_omitted_without_exception():
    query = parse_query(_request())
    malformed = [
        {
            "observed_at": value,
            "payload_hash": str(index) * 64,
            "facts": {"breakdowns": {"models": ["codex"]}},
        }
        for index, value in enumerate((None, 1, {}, []), start=1)
    ]

    result = project(malformed, query, timeseries=False)

    assert result["state"] == "partial"
    assert result["unavailable_reason"] == "malformed_observations_omitted"
    assert result["coverage"] == {"returned": 0, "examined": 4, "malformed": 4}
    assert result["models"] == []


def test_summary_projects_per_node_freshness_version_and_separate_drift():
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    facts = {
        "breakdowns": {"models": ["qwen3.8"], "nodes": ["chiap01", "chiap08"]},
        "daily_token_rows": [{"node": "chiap08", "model": "qwen3.8", "backend": "vllm"}],
        "gateway": {
            "backend_health": {},
            "expected_nodes": ["chiap01", "chiap02", "chiap08"],
            "nodes": {
                "chiap08": {
                    "backend": "vllm",
                    "served_model": "qwen3.8",
                    "transport_profile": "skgateway-local",
                    "runtime_revision": "a" * 40,
                    "version": "0.2.0",
                    "configuration_drift": "clean",
                }
            },
        },
    }
    result = project(
        [{"observed_at": observed.isoformat(), "payload_hash": "a" * 64, "facts": facts}],
        parse_query(_request()),
        timeseries=False,
    )

    nodes = {row["node_id"]: row for row in result["nodes"]}
    assert nodes["chiap08"]["telemetry_state"] == "current"
    assert nodes["chiap08"]["backend"] == "vllm"
    assert nodes["chiap08"]["served_model"] == "qwen3.8"
    assert nodes["chiap08"]["transport_profile"] == "skgateway-local"
    assert nodes["chiap08"]["runtime_revision"] == "a" * 40
    assert nodes["chiap08"]["configuration_drift"] == "clean"
    assert nodes["chiap01"]["telemetry_state"] == "current"
    assert nodes["chiap01"]["backend"] is None
    assert nodes["chiap01"]["configuration_drift"] is None
    assert nodes["chiap02"]["telemetry_state"] == "missing"
    assert nodes["chiap02"]["observed_at"] is None
    assert result["node_totals"] == {
        "named": 3,
        "current": 2,
        "stale": 0,
        "missing": 1,
        "unknown": 0,
    }


def test_per_node_snapshot_marks_old_observation_stale_without_changing_drift():
    observed = datetime.now(timezone.utc) - timedelta(seconds=181)
    facts = {
        "breakdowns": {"models": [], "nodes": ["chiap01"]},
        "gateway": {
            "backend_health": {},
            "nodes": {"chiap01": {"configuration_drift": "clean"}},
        },
    }
    result = project(
        [{"observed_at": observed.isoformat(), "payload_hash": "b" * 64, "facts": facts}],
        parse_query(_request()),
        timeseries=False,
    )

    assert result["nodes"][0]["telemetry_state"] == "stale"
    assert result["nodes"][0]["configuration_drift"] == "clean"


def test_fleet_and_economy_totals_share_canonical_node_coverage():
    coverage = node_coverage(
        ["chiap01", "chiap02", "chiap08"],
        {"chiap01": "current", "chiap08": "stale"},
    )

    assert {
        "named": coverage["expected_nodes"],
        "current": coverage["fresh_collectors"],
        "stale": coverage["stale_collectors"],
        "missing": len(coverage["missing_nodes"]),
    } == {"named": 3, "current": 1, "stale": 1, "missing": 1}


def test_malformed_node_inventory_fails_closed_as_partial():
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    facts = {
        "breakdowns": {"models": [], "nodes": ["chiap01"]},
        "gateway": {"backend_health": {}, "expected_nodes": "chiap01"},
    }

    result = project(
        [{"observed_at": observed.isoformat(), "payload_hash": "c" * 64, "facts": facts}],
        parse_query(_request()),
        timeseries=False,
    )

    assert result["state"] == "partial"
    assert result["nodes"] == []
    assert result["coverage"]["malformed"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("backend", {}),
        ("served_model", []),
        ("transport_profile", True),
        ("runtime_revision", ""),
        ("version", "x" * 129),
        ("gateway_version", False),
    ],
)
def test_malformed_node_detail_values_fail_closed(field, value):
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    facts = {
        "breakdowns": {"models": [], "nodes": ["chiap01"]},
        "gateway": {
            "backend_health": {},
            "nodes": {"chiap01": {field: value}},
        },
    }

    result = project(
        [{"observed_at": observed.isoformat(), "payload_hash": "d" * 64, "facts": facts}],
        parse_query(_request()),
        timeseries=False,
    )

    assert result["state"] == "partial"
    assert result["nodes"] == []
    assert result["summary"] is None


@pytest.mark.parametrize("value", [[], {}, True, "", "x" * 129, "healthy"])
def test_malformed_configuration_drift_fails_closed_without_exception(value):
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    facts = {
        "breakdowns": {"models": [], "nodes": ["chiap01"]},
        "gateway": {
            "backend_health": {},
            "nodes": {"chiap01": {"configuration_drift": value}},
        },
    }

    result = project(
        [{"observed_at": observed.isoformat(), "payload_hash": "e" * 64, "facts": facts}],
        parse_query(_request()),
        timeseries=False,
    )

    assert result["state"] == "partial"
    assert result["nodes"] == []
    assert result["summary"] is None
