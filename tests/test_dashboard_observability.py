from skdashboard.dashboard_observability import _gateway, _vllm, collect_gateway


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
