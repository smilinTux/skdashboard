"""SKCounter 92d745a3 observation contract at the protected dashboard boundary."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from skdashboard.dashboard import create_app
from skdashboard.gateway_api import _facts_are_well_formed, _per_model_snapshot

UNAVAILABLE_APP = {"unavailable": "gateway_surface_does_not_expose_application_attribution"}


@pytest.fixture
def observation():
    # Facts mirror the reviewed SKCounter v2 collector fixture, including the
    # unavailable sentinel which is a legitimate fact, not malformed telemetry.
    return {
        "schema_version": "skcounter.gateway_observation.v2",
        "measurement_lane": "gateway_observed",
        "observed_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
        "payload_hash": "a" * 64,
        "facts": {
            "gateway": {
                "uptime_seconds": 1200,
                "backend_health": {"chiap08-qwen38": {"status": "ok", "errorRate": 0}},
            },
            "requests": {
                "total": 10,
                "active_concurrency": 2,
                "error_count": 1,
                "recent_requests_5m": 30,
                "recent_errors_5m": 0,
                "rate_5m_per_second": 0.1,
            },
            "latency_ms": {
                "chiap08-qwen38/qwen3.8": {
                    "p50": 120,
                    "p95": 400,
                    "p99": 900,
                    "mean": 200,
                    "count": 25,
                }
            },
            "queue": {
                "wait_ms_percentiles": {
                    "unavailable": "gateway_surface_does_not_expose_queue_telemetry"
                },
                "admission_outcomes": {
                    "unavailable": "gateway_surface_does_not_expose_queue_telemetry"
                },
            },
            "rate_limits": {
                "http_429_count": {
                    "unavailable": "gateway_surface_does_not_expose_rate_limit_counts"
                }
            },
            "tokens": {"input": 100, "output": 50, "throughput_5m_per_second": 0.5},
            "generation": {
                "throughput_tokens_per_second": {
                    "unavailable": "gateway_stats_surface_does_not_expose_generation_throughput"
                }
            },
            "cost": {"total_usd": 0.25, "unpriced_requests": 0, "truth": "actual"},
            "breakdowns": {
                "models": ["qwen3.8"],
                "providers": ["local"],
                "nodes": ["chiap08-qwen38"],
                "clients": ["atlas"],
                "apps": UNAVAILABLE_APP,
                "rails": ["local"],
            },
            "daily_token_rows": [
                {
                    "bucket": "2026-09-05",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "request_count": 10,
                    "model": "qwen3.8",
                    "backend": "chiap08-qwen38",
                    "agent": "atlas",
                }
            ],
            "events": {"count": 2, "by_type": {"info": 2}},
            "activity": {"count": 1, "by_type": {}},
        },
    }


def indexed_client(tmp_path, observations, *, root=None):
    root = root or tmp_path / "skcounter"
    root.mkdir()
    index_dir = root / "observation-index"
    index_dir.mkdir()
    entries = []
    for index, observation in enumerate(observations):
        raw = json.dumps(observation).encode()
        name = f"observation-{index}.json"
        (root / name).write_bytes(raw)
        entries.append({"source_path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    (index_dir / "latest.json").write_text(json.dumps({"entries": entries}))
    return TestClient(
        create_app(tmp_path, control_plane_authorizer=lambda bearer, *_: bearer == "reader")
    )


def test_gateway_provider_honors_configured_collector_root(tmp_path, monkeypatch, observation):
    configured_root = tmp_path / "collector-state"
    monkeypatch.setenv("SKCOUNTER_DATA_DIR", str(configured_root))
    client = indexed_client(tmp_path / "dashboard-home", [observation], root=configured_root)

    response = client.get("/api/v1/gateway/summary", headers={"Authorization": "Bearer reader"})

    assert response.status_code == 200
    assert response.json()["coverage"] == {"returned": 1, "examined": 1, "malformed": 0}


@pytest.mark.parametrize("endpoint", ["summary", "timeseries"])
def test_indexed_collector_sentinel_survives_protected_projection(tmp_path, observation, endpoint):
    client = indexed_client(tmp_path, [observation])
    response = client.get(
        f"/api/v1/gateway/{endpoint}", headers={"Authorization": "Bearer reader"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "current"
    assert body["coverage"] == {"returned": 1, "examined": 1, "malformed": 0}
    facts = body["summary"] if endpoint == "summary" else body["items"][0]["facts"]
    assert facts == observation["facts"]
    assert facts["breakdowns"]["apps"] == UNAVAILABLE_APP
    if endpoint == "summary":
        assert [row["model"] for row in body["models"]] == ["qwen3.8"]


@pytest.mark.parametrize("app", ["some-app", "unavailable", UNAVAILABLE_APP["unavailable"]])
@pytest.mark.parametrize("endpoint", ["summary", "timeseries"])
def test_unavailable_attribution_never_matches_sentinel_keys_or_values(
    tmp_path, observation, endpoint, app
):
    client = indexed_client(tmp_path, [observation])
    response = client.get(
        f"/api/v1/gateway/{endpoint}",
        params={"app": app},
        headers={"Authorization": "Bearer reader"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "empty"
    assert body["coverage"] == {"returned": 0, "examined": 1, "malformed": 0}


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"unavailable": None},
        {"unavailable": []},
        {"unavailable": ""},
        {"unavailable": " "},
        {"unavailable": "missing", "unexpected": True},
    ],
)
def test_malformed_sentinel_shapes_remain_rejected(observation, bad):
    observation["facts"]["breakdowns"]["apps"] = bad
    assert not _facts_are_well_formed(observation["facts"])


def test_unavailable_model_names_do_not_create_a_sentinel_model(observation):
    facts = observation["facts"]
    facts["breakdowns"]["models"] = {"unavailable": "models_not_observed"}
    facts["daily_token_rows"] = []
    assert _facts_are_well_formed(facts)
    assert _per_model_snapshot(facts) == []


def test_valid_collector_data_is_preserved_beside_malformed_timestamp(tmp_path, observation):
    client = indexed_client(tmp_path, [observation, {**observation, "observed_at": None}])
    response = client.get("/api/v1/gateway/summary", headers={"Authorization": "Bearer reader"})
    body = response.json()
    assert response.status_code == 200
    assert body["state"] == "partial"
    assert body["coverage"] == {"returned": 1, "examined": 2, "malformed": 1}
    assert body["summary"] == observation["facts"]
