import hashlib
import json
from datetime import datetime

import pytest

from skdashboard.control_plane_adapters import Reader, _local_readers, project_estate
from skdashboard.skperf_aggregate import (
    AGGREGATE_SCHEMA_VERSION,
    SOURCE_SCHEMA_VERSION,
    AggregateError,
    publish,
)

NOW = "2026-09-09T04:30:00Z"


def _source(tmp_path, *, approval="approved", expected=2, benchmarks=None):
    document = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "approval_state": approval,
        "observed_at": "2026-09-09T04:00:00Z",
        "expected_benchmarks": expected,
        "benchmarks": benchmarks
        if benchmarks is not None
        else [
            {"benchmark_id": "baseline", "state": "ok", "capacity_pressure": 0.4},
            {"benchmark_id": "current", "state": "regression", "capacity_pressure": 0.8},
        ],
    }
    path = tmp_path / "approved.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_publish_produces_versioned_bounded_now_aggregate(tmp_path):
    source = _source(tmp_path)
    destination = tmp_path / "skperf/data/aggregate.json"

    result = publish(source, destination, produced_at=NOW)

    assert result == json.loads(destination.read_text())
    assert result["schema_version"] == AGGREGATE_SCHEMA_VERSION
    assert result["population"] == "approved_benchmarks"
    assert result["produced_at"] == NOW
    assert result["reporting_benchmarks"] == result["expected_benchmarks"] == 2
    assert result["regressions"] == 1
    assert result["capacity_pressure"] == 0.8
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "benchmarks" not in result
    assert "benchmark_id" not in destination.read_text()

    reader = _local_readers(tmp_path, board_data={})["skperf.aggregate"]
    projected = next(
        item
        for item in project_estate(
            {"skperf.aggregate": Reader(payload=reader())},
            now=datetime.fromisoformat(NOW.replace("Z", "+00:00")),
        )
        if item["adapter_id"] == "skperf.aggregate"
    )
    assert projected["truth_state"] == "current"
    assert projected["coverage"] == {"expected": 2, "reporting": 2}
    assert projected["aggregate"]["regressions"] == 1


def test_publish_no_data_is_distinct_from_unavailable(tmp_path):
    source = _source(tmp_path, expected=0, benchmarks=[])
    destination = tmp_path / "skperf/data/aggregate.json"
    publish(source, destination, produced_at=NOW)
    reader = _local_readers(tmp_path, board_data={})["skperf.aggregate"]

    no_data = next(
        item
        for item in project_estate(
            {"skperf.aggregate": Reader(payload=reader())},
            now=datetime.fromisoformat(NOW.replace("Z", "+00:00")),
        )
        if item["adapter_id"] == "skperf.aggregate"
    )
    unavailable = next(
        item
        for item in project_estate(
            {"skperf.aggregate": Reader(failure="unavailable")},
            now=datetime.fromisoformat(NOW.replace("Z", "+00:00")),
        )
        if item["adapter_id"] == "skperf.aggregate"
    )
    assert no_data["truth_state"] == "unknown"
    assert no_data["coverage"] == {"expected": 0, "reporting": 0}
    assert unavailable["truth_state"] == "unavailable"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(approval_state="pending"),
        lambda value: value["benchmarks"].append(value["benchmarks"][0]),
        lambda value: value["benchmarks"][0].update(capacity_pressure=1.1),
    ],
)
def test_publish_rejects_unapproved_or_malformed_sources(tmp_path, mutation):
    source = _source(tmp_path)
    document = json.loads(source.read_text())
    mutation(document)
    source.write_text(json.dumps(document))

    with pytest.raises(AggregateError):
        publish(source, tmp_path / "aggregate.json", produced_at=NOW)
