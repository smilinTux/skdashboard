import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from skdashboard.gateway_api import _per_model_snapshot, parse_query, project


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
    assert row["backends"] == [
        {"backend": "chiap08-qwen38", "health": {"status": "ok"}}
    ]
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
