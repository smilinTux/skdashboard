"""Publish an approved, summary-only SKPerf aggregate for NOW."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_SCHEMA_VERSION = "skperf.approved-benchmarks@1.0.0"
AGGREGATE_SCHEMA_VERSION = "skperf.aggregate@1.0.0"
MAX_SOURCE_BYTES = 1_048_576
MAX_BENCHMARKS = 2_048


class AggregateError(ValueError):
    """The approved source cannot safely produce an aggregate."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise AggregateError("source observed_at is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AggregateError("source observed_at is invalid") from exc
    if parsed.tzinfo is None:
        raise AggregateError("source observed_at is invalid")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_source(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise AggregateError("approved benchmark source is unavailable")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise AggregateError("approved benchmark source size is invalid")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AggregateError("approved benchmark source is malformed") from exc
    if not isinstance(document, dict):
        raise AggregateError("approved benchmark source is malformed")
    return document, hashlib.sha256(raw).hexdigest()


def build(source: Path, *, produced_at: str | None = None) -> dict[str, Any]:
    """Reduce one approved summary document to the NOW file contract."""
    document, source_sha256 = _read_source(source)
    if set(document) != {
        "schema_version",
        "approval_state",
        "observed_at",
        "expected_benchmarks",
        "benchmarks",
    }:
        raise AggregateError("approved benchmark source fields do not match v1")
    if document["schema_version"] != SOURCE_SCHEMA_VERSION:
        raise AggregateError("approved benchmark source schema is unsupported")
    if document["approval_state"] != "approved":
        raise AggregateError("benchmark source is not approved")
    source_observed_at = _timestamp(document["observed_at"])
    expected = document["expected_benchmarks"]
    benchmarks = document["benchmarks"]
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected < 0
        or expected > MAX_BENCHMARKS
        or not isinstance(benchmarks, list)
        or len(benchmarks) > expected
    ):
        raise AggregateError("approved benchmark population is invalid")

    regressions = 0
    pressures: list[float] = []
    seen: set[str] = set()
    for item in benchmarks:
        if not isinstance(item, dict) or set(item) != {
            "benchmark_id",
            "state",
            "capacity_pressure",
        }:
            raise AggregateError("approved benchmark summary is malformed")
        benchmark_id = item["benchmark_id"]
        state = item["state"]
        pressure = item["capacity_pressure"]
        if (
            not isinstance(benchmark_id, str)
            or not benchmark_id
            or len(benchmark_id) > 128
            or benchmark_id in seen
            or state not in {"ok", "regression"}
            or not isinstance(pressure, (int, float))
            or isinstance(pressure, bool)
            or not math.isfinite(pressure)
            or not 0 <= pressure <= 1
        ):
            raise AggregateError("approved benchmark summary is malformed")
        seen.add(benchmark_id)
        regressions += state == "regression"
        pressures.append(float(pressure))

    timestamp = _timestamp(
        produced_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    return {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "population": "approved_benchmarks",
        "produced_at": timestamp,
        "source_observed_at": source_observed_at,
        "source_sha256": source_sha256,
        "regressions": regressions,
        "capacity_pressure": max(pressures, default=0.0),
        "reporting_benchmarks": len(benchmarks),
        "expected_benchmarks": expected,
        "errors": [] if len(benchmarks) == expected else ["benchmark_coverage_incomplete"],
    }


def publish(source: Path, destination: Path, *, produced_at: str | None = None) -> dict[str, Any]:
    """Build and atomically replace the mutable latest aggregate."""
    document = build(source, produced_at=produced_at)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(document) + b"\n")
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = publish(args.approved_source, args.destination)
    except (AggregateError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "schema_version": document["schema_version"],
                "reporting_benchmarks": document["reporting_benchmarks"],
                "regressions": document["regressions"],
                "source_sha256": document["source_sha256"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
