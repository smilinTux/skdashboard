import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from skdashboard.gateway_api import _per_model_snapshot, parse_query, project


def _request(query=None):
    return SimpleNamespace(query_params=query or {}, state=SimpleNamespace())


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
