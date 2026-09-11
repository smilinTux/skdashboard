from skdashboard.dashboard_observability import (
    _fleet,
    _gateway,
    _vllm,
    collect,
    collect_gateway,
)


def test_vllm_projection_preserves_operational_and_cache_metrics():
    raw = """
vllm:num_requests_running{model_name="qwen",engine="0"} 2
vllm:num_requests_waiting{model_name="qwen",engine="0"} 3
vllm:kv_cache_usage_perc{model_name="qwen",engine="0"} 42.5
vllm:prefix_cache_hits_total{model_name="qwen",engine="0"} 8
vllm:prompt_tokens_cached_total{model_name="qwen",engine="0"} 100
"""
    result = _vllm(raw)
    assert result["summary"]["running"] == 2
    assert result["summary"]["queued"] == 3
    assert result["summary"]["kv_cache_usage_percent"] == 42.5
    assert result["summary"]["prefix_cache_hits"] == 8
    assert result["models"] == ["qwen"]


def test_gateway_projection_keeps_backend_and_attribution():
    result = _gateway('{"status":"ok","backends":{"chiap08":{"status":"up"}},"pool":{"totalQueued":1},"metrics":{"totalRequests":7,"costByModel":{"qwen":{"requests":7}},"latency":{"qwen":{"p95":12}}}}')
    assert result["backends"]["chiap08"]["status"] == "up"
    assert result["pool"]["totalQueued"] == 1
    assert result["summary"]["totalRequests"] == 7
    assert result["models"]["qwen"]["requests"] == 7


def test_gateway_collection_is_independent_from_vllm(monkeypatch):
    requested = {}

    def fetch(url, *, timeout):
        requested.update(url=url, timeout=timeout)
        return '{"status":"ok","metrics":{"totalRequests":7}}', None

    monkeypatch.setattr(
        "skdashboard.dashboard_observability._fetch",
        fetch,
    )

    result = collect_gateway()

    assert result["source"]["summary"]["totalRequests"] == 7
    assert result["errors"] == []
    assert requested["url"].endswith(":18790/status")
    assert requested["timeout"] < 1


def test_gateway_collection_preserves_unavailable_state(monkeypatch):
    monkeypatch.setattr(
        "skdashboard.dashboard_observability._fetch",
        lambda _url, *, timeout: (None, f"timed out after {timeout}"),
    )

    result = collect_gateway()

    assert result["source"] is None
    assert result["errors"] == ["skgateway: timed out after 0.75"]


def test_local_fleet_projection_is_bounded_redacted_and_freshness_driven(
    tmp_path, monkeypatch
):
    workers = [
        {
            "name": "pi-glm-chiap08-card",
            "host": "chiap08",
            "state": "working",
            "task_id": "d9a10009",
            "task_title": "must not leave the server",
            "observed_at": "2026-09-10T20:00:00Z",
            "age_seconds": 4,
            "truth_state": "current",
            "notes": "secret",
            "prompt": "secret",
        },
        {
            "name": "pi-glm-chiap02-old",
            "host": "chiap02",
            "state": "working",
            "task_id": "25ab78c6",
            "observed_at": "2026-09-10T19:00:00Z",
            "age_seconds": 3604,
            "truth_state": "stale",
        },
    ]
    monkeypatch.setattr(
        "skdashboard.dashboard_fleet.collect_workers",
        lambda _home: {"workers": workers, "errors": []},
    )

    result = _fleet(tmp_path)

    assert result["summary"] == {
        "running": 1,
        "stale": 1,
        "unavailable": 0,
        "total": 2,
        "truncated": False,
    }
    assert result["workers"][1]["state"] == "working"
    assert result["workers"][1]["truth_state"] == "stale"
    assert "task_title" not in result["workers"][0]
    assert "notes" not in result["workers"][0]
    assert "prompt" not in result["workers"][0]


def test_local_fleet_projection_caps_worker_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "skdashboard.dashboard_fleet.collect_workers",
        lambda _home: {
            "workers": [
                {"name": f"worker-{number}", "truth_state": "stale"}
                for number in range(300)
            ],
            "errors": [],
        },
    )

    result = _fleet(tmp_path)

    assert len(result["workers"]) == 256
    assert result["summary"]["running"] == 0
    assert result["summary"]["stale"] == 256
    assert result["summary"]["truncated"] is True


def test_local_fleet_projection_does_not_expose_source_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "skdashboard.dashboard_fleet.collect_workers",
        lambda _home: {"workers": [], "errors": ["secret=/private/token"]},
    )

    result = _fleet(tmp_path)

    assert result["errors"] == ["worker projection unavailable"]
    assert "private" not in str(result)


def test_collect_uses_local_fleet_when_endpoint_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("SKDASHBOARD_FLEET_METRICS_ENDPOINT", raising=False)
    monkeypatch.setattr(
        "skdashboard.dashboard_observability._fetch",
        lambda _url, *, timeout=2.5: (None, "unavailable"),
    )
    monkeypatch.setattr(
        "skdashboard.dashboard_observability._fleet",
        lambda home: {
            "source": "skcapstone_fleet",
            "truth_state": "current",
            "workers": [],
            "summary": {"running": 0, "stale": 0, "total": 0},
            "errors": [],
        },
    )

    result = collect(tmp_path)

    assert result["sources"] == [
        {
            "source": "skcapstone_fleet",
            "truth_state": "current",
            "workers": [],
            "summary": {"running": 0, "stale": 0, "total": 0},
            "errors": [],
        }
    ]
