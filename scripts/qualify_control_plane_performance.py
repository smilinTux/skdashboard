#!/usr/bin/env python3
"""Bounded public-synthetic SKCP-51 qualification against the checked-out candidate."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import subprocess
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from skdashboard.control_plane_api import routes

ROOT = Path(__file__).resolve().parents[1]
ESTATE_RECORDS = 10_000
WARMUP_REQUESTS = 10
MEASURED_REQUESTS = 100
READ_HEADERS = {"Authorization": "Bearer public-synthetic-read"}


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def cpu_model() -> str:
    for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def fixture_hash() -> str:
    path = ROOT / "tests/fixtures/control_plane_full_estate.v1.0.0.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_app(counter: dict[str, int]) -> Starlette:
    records = [
        {
            "id": f"synthetic-{index:05d}",
            "title": f"Public synthetic task {index}",
            "priority": "medium",
            "status": "open",
            "claimed_by": None,
        }
        for index in range(ESTATE_RECORDS)
    ]

    def board_reader(_home: Path) -> dict:
        counter["calls"] += 1
        counter["records_returned"] += len(records)
        return {
            "tasks": records,
            "summary": {
                "total": len(records),
                "done": 0,
                "open": len(records),
                "in_progress": 0,
            },
        }

    def health_reader(_home: Path) -> dict:
        return {"consciousness": "healthy", "pillars": {}}

    return Starlette(
        routes=routes(
            Path("/public-synthetic/skcp-51"),
            board_reader=board_reader,
            health_reader=health_reader,
            authorizer=lambda bearer, capability, _target: (
                bearer == "public-synthetic-read" and capability == "skdashboard.read"
            ),
        )
    )


def timed_pagination() -> dict:
    counter = {"calls": 0, "records_returned": 0}
    client = TestClient(make_app(counter))
    path = "/api/v1/board/summary?limit=1"
    for _ in range(WARMUP_REQUESTS):
        response = client.get(path, headers=READ_HEADERS)
        if response.status_code != 200 or len(response.json().get("items", [])) != 1:
            raise RuntimeError("pagination warmup failed")
    latencies: list[float] = []
    errors = 0
    start_cpu = time.process_time()
    start_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for _ in range(MEASURED_REQUESTS):
        started = time.perf_counter_ns()
        response = client.get(path, headers=READ_HEADERS)
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        if response.status_code != 200 or len(response.json().get("items", [])) != 1:
            errors += 1
    cpu_seconds = time.process_time() - start_cpu
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "load": {
            "estate_records": ESTATE_RECORDS,
            "requested_limit": 1,
            "warmup_requests": WARMUP_REQUESTS,
            "measured_requests": MEASURED_REQUESTS,
            "concurrency": 1,
        },
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies),
        },
        "errors": errors,
        "error_rate": errors / MEASURED_REQUESTS,
        "cpu_seconds": cpu_seconds,
        "peak_rss_kib": peak_rss,
        "rss_growth_kib": max(0, peak_rss - start_rss),
        "owner_calls_total": counter["calls"],
        "owner_records_returned_total": counter["records_returned"],
        "owner_records_per_limit_one_request": counter["records_returned"] // counter["calls"],
    }


def conditional_read_probe() -> dict:
    counter = {"calls": 0, "records_returned": 0}
    client = TestClient(make_app(counter))
    path = "/api/v1/board/summary?limit=1"
    first = client.get(path, headers=READ_HEADERS)
    etag = first.headers["etag"]
    second = client.get(path, headers={**READ_HEADERS, "If-None-Match": etag})
    return {
        "baseline_status": first.status_code,
        "conditional_status": second.status_code,
        "etag": etag,
        "owner_calls": counter["calls"],
        "owner_records_returned": counter["records_returned"],
        "cache_hit_avoided_owner_read": counter["calls"] == 1,
    }


def rate_isolation_probe() -> dict:
    counter = {"calls": 0, "records_returned": 0}
    client = TestClient(make_app(counter))
    statuses = [
        client.get("/api/v1/board/summary?limit=1", headers=READ_HEADERS).status_code
        for _ in range(120)
    ]
    health = client.get("/api/v1/health")
    return {
        "common_read_requests": 120,
        "common_read_status_counts": {str(code): statuses.count(code) for code in set(statuses)},
        "critical_health_status_after_saturation": health.status_code,
        "critical_health_error_code": health.json().get("code"),
        "critical_health_isolated": health.status_code == 200,
        "owner_calls": counter["calls"],
        "owner_records_returned": counter["records_returned"],
    }


def main() -> None:
    qualification_started = time.perf_counter()
    qualification_cpu_started = time.process_time()
    head = git("rev-parse", "HEAD")
    origin_main = git("rev-parse", "origin/main")
    if head != origin_main:
        raise SystemExit("refusing qualification: HEAD is not current local origin/main")
    result = {
        "card": "2d02b6ed",
        "candidate": {"head": head, "origin_main": origin_main, "equal": True},
        "environment": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_model": cpu_model(),
            "logical_cpus": os.cpu_count(),
            "fixture_sha256": fixture_hash(),
            "public_synthetic_only": True,
            "transport": "Starlette TestClient in-process ASGI",
        },
        "pagination": timed_pagination(),
        "conditional_read": conditional_read_probe(),
        "rate_isolation": rate_isolation_probe(),
    }
    blockers = []
    if result["pagination"]["owner_records_per_limit_one_request"] > 2:
        blockers.append("limit=1 performs a 10000-record owner read")
    if not result["conditional_read"]["cache_hit_avoided_owner_read"]:
        blockers.append("ETag 304 recomputes the full owner projection")
    if not result["rate_isolation"]["critical_health_isolated"]:
        blockers.append("common-read saturation rate-limits critical health")
    result["verdict"] = "FAIL" if blockers else "PASS"
    result["blockers"] = blockers
    result["qualification_process"] = {
        "wall_seconds": time.perf_counter() - qualification_started,
        "cpu_seconds": time.process_time() - qualification_cpu_started,
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(1 if blockers else 0)


if __name__ == "__main__":
    main()
