import hashlib
import json
from pathlib import Path

import pytest

from skdashboard.dashboard_skcounter import SnapshotError, get_ai_usage
from skdashboard.skcounter_gateway_compose import compose


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _snapshot(lane="gateway_observed"):
    base = {
        "schema_version": "skcounter.snapshot.v1",
        "measurement_lane": lane,
        "node_id": "chiap01",
        "principal_id": "skgateway",
        "collector": {
            "product": "skcounter",
            "facade_version": "0.2.0",
            "backend": "skgateway",
            "backend_version": "1",
        },
        "observed_at": "2026-09-07T12:00:00Z",
        "bucket_timezone": "UTC",
        "window": {"start": "2026-09-07T00:00:00Z", "end": "2026-09-07T23:59:59Z"},
        "source_state_digest": "a" * 64,
        "aggregates": [
            {
                "view": "models",
                "bucket_start": "2026-09-07T00:00:00Z",
                "client": "skgateway",
                "provider": "openai",
                "model": "gpt",
                "tokens": {
                    "input": 2,
                    "output": 3,
                    "cache_read": 0,
                    "cache_write": 0,
                    "reasoning": 0,
                    "total": 5,
                },
                "message_count": 1,
            }
        ],
    }
    digest = hashlib.sha256(_canonical(base)).hexdigest()
    return {**base, "idempotency_key": digest, "payload_hash": digest}


def _producer(tmp_path: Path, observation=None):
    source = tmp_path / "sent" / "chiap01/skgateway/a.json"
    source.parent.mkdir(parents=True)
    observation = observation or _snapshot()
    source.write_bytes(_canonical(observation) + b"\n")
    entry = {
        "schema_version": "skcounter.latest-observation-index.v1",
        "key": "key",
        "measurement_lane": observation["measurement_lane"],
        "node_id": observation["node_id"],
        "principal_id": observation["principal_id"],
        "view": "models",
        "bucket_start": "2026-09-07T00:00:00Z",
        "observation_path": "chiap01/skgateway/a.json",
        "observed_at": observation["observed_at"],
        "payload_hash": observation["payload_hash"],
        "idempotency_key": observation["idempotency_key"],
    }
    index = tmp_path / "latest-observation-index.jsonl"
    index.write_text(json.dumps(entry) + "\n")
    return index, tmp_path / "sent"


def test_compose_publishes_hash_verified_rows_consumed_by_dashboard(tmp_path, monkeypatch):
    index, sent = _producer(tmp_path)
    destination = tmp_path / "dashboard"
    document = compose(index, sent, destination)
    monkeypatch.setenv("SKCOUNTER_DATA_DIR", str(destination))
    result = get_ai_usage(tmp_path, {"lane": "gateway_observed"})
    assert len(document["entries"]) == 1
    assert result["status"] == "current"
    assert result["summary"]["tokens"]["total"] == 5
    assert result["available_lanes"] == ["gateway_observed"]


@pytest.mark.parametrize("mutation", ["lane", "path", "provenance"])
def test_compose_rejects_untrusted_producer_input(tmp_path, mutation):
    observation = _snapshot("harness_reported" if mutation == "lane" else "gateway_observed")
    index, sent = _producer(tmp_path, observation)
    entry = json.loads(index.read_text())
    if mutation == "path":
        entry["observation_path"] = "../escape.json"
    elif mutation == "provenance":
        entry["payload_hash"] = "b" * 64
    index.write_text(json.dumps(entry) + "\n")
    with pytest.raises(SnapshotError):
        compose(index, sent, tmp_path / "dashboard")


def test_compose_refuses_to_replace_immutable_source(tmp_path):
    index, sent = _producer(tmp_path)
    destination = tmp_path / "dashboard"
    compose(index, sent, destination)
    source = next((destination / "observations").rglob("*.json"))
    source.chmod(0o600)
    source.write_text("changed")
    with pytest.raises(SnapshotError, match="conflicts"):
        compose(index, sent, destination)
