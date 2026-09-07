"""Tests for the read-only SKCounter projection in the Economy workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skdashboard import dashboard_skcounter
from skdashboard.dashboard import create_app
from skdashboard.dashboard_itil import get_reliability_projection
from skdashboard.dashboard_skcounter import get_ai_usage
from skdashboard.gateway_api import parse_query, project

DIGEST = "a" * 64


def _aggregate(
    *,
    view="models",
    bucket="2026-08-23T00:00:00Z",
    client="codex",
    provider="openai",
    model="gpt-5.6-sol",
    total=100,
    cost=1.25,
):
    return {
        "view": view,
        "bucket_start": bucket,
        "client": client,
        "provider": provider,
        "model": model,
        "tokens": {
            "input": total // 10,
            "output": total // 10,
            "cache_read": total - 2 * (total // 10),
            "cache_write": 0,
            "reasoning": total // 20,
            "total": total,
        },
        "message_count": 4,
        "cost": {
            "amount": cost,
            "currency": "USD",
            "estimated": True,
            "pricing_revision": "fixture-pricing-v1",
        },
        "performance": {
            "duration_ms": 500,
            "timed_tokens": total,
            "sample_count": 4,
            "token_coverage": 1.0,
            "ms_per_1k_tokens": 5000.0,
        },
    }


def _snapshot(
    *,
    lane="harness_reported",
    node="chiap08",
    principal="jarvis",
    observed="2026-08-23T12:00:00Z",
    aggregates=None,
):
    return {
        "schema_version": "skcounter.snapshot.v1",
        "idempotency_key": DIGEST,
        "measurement_lane": lane,
        "node_id": node,
        "principal_id": principal,
        "collector": {
            "product": "skcounter",
            "facade_version": "0.1.0",
            "backend": "tokscale",
            "backend_version": "4.13.0",
        },
        "observed_at": observed,
        "bucket_timezone": "America/Chicago",
        "window": {
            "start": "2026-08-23T00:00:00Z",
            "end": "2026-08-24T00:00:00Z",
        },
        "source_state_digest": DIGEST,
        "aggregates": aggregates or [_aggregate()],
        "payload_hash": DIGEST,
    }


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "skcounter"
    (root / "observations").mkdir(parents=True)
    (root / "observation-index").mkdir(parents=True)
    monkeypatch.setenv("SKCOUNTER_DATA_DIR", str(root))
    _write_index(root, [])
    return root


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _write_index(root: Path, entries: list[dict]):
    unsigned = {
        "schema_version": "skcounter.latest-observation-index.v1",
        "entries": sorted(
            entries,
            key=lambda item: (
                item["measurement_lane"],
                item["node_id"],
                item["principal_id"],
                item["view"],
                item["bucket_start"],
            ),
        ),
    }
    document = {
        **unsigned,
        "index_sha256": hashlib.sha256(_canonical(unsigned)).hexdigest(),
    }
    (root / "observation-index" / "latest.json").write_bytes(_canonical(document) + b"\n")


def _rebuild_index(root: Path):
    winners = {}
    for path in sorted((root / "observations").rglob("*.json")):
        raw = path.read_bytes()
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for aggregate in document.get("aggregates", []):
            if not isinstance(aggregate, dict) or not aggregate.get("view") or not aggregate.get(
                "bucket_start"
            ):
                continue
            entry = {
                "measurement_lane": document.get("measurement_lane"),
                "node_id": document.get("node_id"),
                "principal_id": document.get("principal_id"),
                "view": aggregate["view"],
                "bucket_start": aggregate["bucket_start"],
                "observed_at": document.get("observed_at"),
                "idempotency_key": document.get("idempotency_key"),
                "payload_hash": document.get("payload_hash"),
                "source_path": path.relative_to(root).as_posix(),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            }
            key = tuple(entry[field] for field in (
                "measurement_lane",
                "node_id",
                "principal_id",
                "view",
                "bucket_start",
            ))
            prior = winners.get(key)
            if prior is None or (entry["observed_at"], entry["idempotency_key"]) > (
                prior["observed_at"],
                prior["idempotency_key"],
            ):
                winners[key] = entry
    _write_index(root, list(winners.values()))


def _write(root: Path, name: str, document: dict):
    path = root / "observations" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    _rebuild_index(root)


def test_empty_projection_is_well_formed(data_root, tmp_path):
    result = get_ai_usage(tmp_path)

    assert result["status"] == "empty"
    assert result["summary"]["tokens"]["total"] == 0
    assert result["series"] == []
    assert result["collectors"] == []
    assert result["errors"] == []
    assert result["index"]["status"] == "empty"
    assert result["sources"] == []


def test_acknowledged_edge_store_reads_only_latest_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "skcounter"
    sent = root / "sent" / "chiap08" / "jarvis"
    sent.mkdir(parents=True)
    monkeypatch.setenv("SKCOUNTER_DATA_DIR", str(root))
    (sent / "2026-08-23T110000Z-old.json").write_text(
        json.dumps(_snapshot(observed="2026-08-23T11:00:00Z", aggregates=[_aggregate(total=100)])),
        encoding="utf-8",
    )
    (sent / "2026-08-23T120000Z-new.json").write_text(
        json.dumps(_snapshot(observed="2026-08-23T12:00:00Z", aggregates=[_aggregate(total=250)])),
        encoding="utf-8",
    )

    result = get_ai_usage(
        tmp_path,
        now=datetime(2026, 8, 23, 12, 10, tzinfo=timezone.utc),
    )

    assert result["observation_count"] == 1
    assert result["summary"]["tokens"]["total"] == 250


def test_duplicate_observation_and_acknowledged_snapshot_is_counted_once(
    data_root, tmp_path
):
    snapshot = _snapshot()
    _write(data_root, "received.json", snapshot)
    sent = data_root / "sent" / "chiap08" / "jarvis"
    sent.mkdir(parents=True)
    (sent / "acknowledged.json").write_text(json.dumps(snapshot), encoding="utf-8")

    result = get_ai_usage(tmp_path)

    assert result["observation_count"] == 1
    assert result["summary"]["tokens"]["total"] == 100


def test_lanes_remain_separate_and_latest_observation_wins(data_root, tmp_path):
    _write(
        data_root,
        "harness-old.json",
        _snapshot(observed="2026-08-23T11:00:00Z", aggregates=[_aggregate(total=100)]),
    )
    _write(
        data_root,
        "harness-new.json",
        _snapshot(observed="2026-08-23T12:00:00Z", aggregates=[_aggregate(total=250)]),
    )
    _write(
        data_root,
        "gateway.json",
        _snapshot(
            lane="gateway_observed",
            principal="skgateway",
            aggregates=[_aggregate(total=900, provider="skgateway")],
        ),
    )

    harness = get_ai_usage(
        tmp_path,
        now=datetime(2026, 8, 23, 12, 10, tzinfo=timezone.utc),
    )
    gateway = get_ai_usage(
        tmp_path,
        {"lane": "gateway_observed"},
        now=datetime(2026, 8, 23, 12, 10, tzinfo=timezone.utc),
    )

    assert harness["summary"]["tokens"]["total"] == 250
    assert gateway["summary"]["tokens"]["total"] == 900
    assert harness["available_lanes"] == ["gateway_observed", "harness_reported"]
    assert harness["collectors"][0]["status"] == "fresh"
    assert harness["coverage"]["expected_nodes"] == 1
    assert harness["coverage"]["percent"] == 100.0


def test_unobserved_lane_does_not_inherit_other_lane_status(data_root, tmp_path):
    _write(data_root, "harness.json", _snapshot())

    gateway = get_ai_usage(tmp_path, {"lane": "gateway_observed"})

    assert gateway["status"] == "empty"
    assert gateway["observation_count"] == 0
    assert gateway["summary"]["tokens"]["total"] == 0


def test_daily_series_breakdowns_filters_and_activity(data_root, tmp_path):
    rows = [
        _aggregate(total=300),
        _aggregate(total=200, client="pi", provider="skgateway", model="sk-codex"),
        _aggregate(view="daily", total=500),
        {
            **_aggregate(view="time_metrics", total=0, cost=0),
            "activity": {
                "active_seconds": 3600,
                "longest_continuous_seconds": 900,
                "max_concurrent": 3,
            },
        },
    ]
    _write(data_root, "usage.json", _snapshot(aggregates=rows))

    all_usage = get_ai_usage(tmp_path)
    codex_only = get_ai_usage(tmp_path, {"client": "codex"})

    assert all_usage["summary"]["tokens"]["total"] == 500
    assert all_usage["summary"]["active_seconds"] == 3600
    assert all_usage["series"][0]["tokens"]["total"] == 500
    assert [row["model"] for row in all_usage["breakdowns"]["models"]] == [
        "gpt-5.6-sol",
        "sk-codex",
    ]
    assert codex_only["summary"]["tokens"]["total"] == 300
    assert set(all_usage["facets"]["clients"]) == {"codex", "pi"}


def test_malformed_raw_data_is_rejected_without_breaking_valid_projection(data_root, tmp_path):
    _write(data_root, "valid.json", _snapshot())
    invalid = _snapshot(node="chiap04")
    invalid["aggregates"][0]["prompt"] = "do not display this"
    _write(data_root, "invalid.json", invalid)

    result = get_ai_usage(tmp_path)

    assert result["status"] == "degraded"
    assert result["summary"]["tokens"]["total"] == 100
    assert len(result["errors"]) == 1
    assert "prohibited raw-data field" in result["errors"][0]
    assert "do not display" not in result["errors"][0]


def test_reader_uses_index_without_recursive_observation_discovery(
    data_root, tmp_path, monkeypatch
):
    _write(data_root, "nested/usage.json", _snapshot())

    def reject_rglob(*_args, **_kwargs):
        raise AssertionError("request attempted recursive observation discovery")

    monkeypatch.setattr(Path, "rglob", reject_rglob)
    result = get_ai_usage(tmp_path)

    assert result["status"] == "current"
    assert result["summary"]["tokens"]["total"] == 100
    assert result["index"]["entry_count"] == 1
    assert result["sources"][0]["source_path"] == "observations/nested/usage.json"
    assert result["sources"][0]["source_sha256"] == hashlib.sha256(
        (data_root / "observations/nested/usage.json").read_bytes()
    ).hexdigest()
    assert result["sources"][0]["observed_at"] == "2026-08-23T12:00:00Z"


def test_missing_and_corrupt_indexes_are_unavailable_without_recursive_fallback(
    data_root, tmp_path
):
    _write(data_root, "usage.json", _snapshot())
    index_path = data_root / "observation-index" / "latest.json"
    index_path.unlink()

    missing = get_ai_usage(tmp_path)

    assert missing["status"] == "degraded"
    assert missing["index"]["status"] == "unavailable"
    assert missing["summary"]["tokens"]["total"] == 0
    assert "unavailable" in missing["errors"][0]

    index_path.write_text('{"schema_version":', encoding="utf-8")
    corrupt = get_ai_usage(tmp_path)

    assert corrupt["status"] == "degraded"
    assert corrupt["index"]["status"] == "unavailable"
    assert corrupt["summary"]["tokens"]["total"] == 0
    assert "malformed" in corrupt["errors"][0]


def test_partial_index_preserves_valid_source_and_marks_missing_source(
    data_root, tmp_path
):
    _write(data_root, "valid.json", _snapshot())
    document = json.loads((data_root / "observation-index" / "latest.json").read_text())
    missing = {
        **document["entries"][0],
        "measurement_lane": "gateway_observed",
        "principal_id": "skgateway",
        "source_path": "observations/missing.json",
        "source_sha256": "b" * 64,
    }
    _write_index(data_root, [*document["entries"], missing])

    result = get_ai_usage(tmp_path)

    assert result["status"] == "degraded"
    assert result["index"]["status"] == "partial"
    assert result["summary"]["tokens"]["total"] == 100
    assert result["observation_count"] == 1
    assert "observations/missing.json" in result["errors"][0]


def test_persistently_changing_index_fails_closed_after_bounded_retries(
    data_root, tmp_path, monkeypatch
):
    _write(data_root, "usage.json", _snapshot())
    original = dashboard_skcounter._read_index_generation
    calls = 0

    def changing(root):
        nonlocal calls
        calls += 1
        document, raw = original(root)
        return document, raw + str(calls).encode()

    monkeypatch.setattr(dashboard_skcounter, "_read_index_generation", changing)
    result = get_ai_usage(tmp_path)

    assert calls == 6
    assert result["status"] == "degraded"
    assert result["index"]["status"] == "changing"
    assert result["summary"]["tokens"]["total"] == 0
    assert result["sources"] == []
    assert "changed during bounded read" in result["errors"][0]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda snapshot: snapshot.update({"bucket_timezone": ""}), "bucket_timezone"),
        (
            lambda snapshot: snapshot.update(
                {"window": {"start": "2026-08-24T00:00:00Z", "end": "2026-08-23T00:00:00Z"}}
            ),
            "window.start cannot be after window.end",
        ),
        (lambda snapshot: snapshot.update({"unexpected": "raw"}), "unsupported fields"),
    ],
)
def test_invalid_envelope_fields_fail_closed(data_root, tmp_path, mutation, message):
    invalid = _snapshot()
    mutation(invalid)
    _write(data_root, "invalid-envelope.json", invalid)

    result = get_ai_usage(tmp_path)

    assert result["status"] == "degraded"
    assert result["summary"]["tokens"]["total"] == 0
    assert message in result["errors"][0]


def test_stale_and_missing_coverage_are_visible(data_root, tmp_path, monkeypatch):
    _write(data_root, "old.json", _snapshot(observed="2026-08-20T12:00:00Z"))
    monkeypatch.setenv("SKCOUNTER_EXPECTED_NODES", "chiap01,chiap04,chiap08")

    result = get_ai_usage(
        tmp_path,
        now=datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
    )

    assert result["collectors"][0]["status"] == "stale"
    assert result["coverage"]["expected_nodes"] == 3
    assert result["coverage"]["reporting_nodes"] == 1
    assert result["coverage"]["missing_nodes"] == ["chiap01", "chiap04"]
    assert result["coverage"]["percent"] == pytest.approx(33.3)


def test_gateway_coverage_uses_its_own_eligible_node_inventory(
    data_root, tmp_path, monkeypatch
):
    _write(
        data_root,
        "gateway.json",
        _snapshot(lane="gateway_observed", node="chiap01", principal="skgateway"),
    )
    monkeypatch.setenv("SKCOUNTER_EXPECTED_NODES", "chiap01,chiap04,chiap08")
    monkeypatch.setenv("SKCOUNTER_EXPECTED_GATEWAY_NODES", "chiap01")

    result = get_ai_usage(
        tmp_path,
        {"lane": "gateway_observed"},
        now=datetime(2026, 8, 23, 12, 10, tzinfo=timezone.utc),
    )

    assert result["coverage"] == {
        "expected_nodes": 1,
        "reporting_nodes": 1,
        "fresh_collectors": 1,
        "delayed_collectors": 0,
        "stale_collectors": 0,
        "missing_nodes": [],
        "percent": 100.0,
    }


def test_economy_fleet_and_reliability_share_actual_node_totals(
    data_root, tmp_path, monkeypatch
):
    now = datetime.now(timezone.utc)
    observed = now.isoformat().replace("+00:00", "Z")
    _write(
        data_root,
        "gateway.json",
        _snapshot(
            lane="gateway_observed",
            node="chiap01",
            principal="skgateway",
            observed=observed,
        ),
    )
    monkeypatch.setenv("SKCOUNTER_EXPECTED_GATEWAY_NODES", "chiap01,chiap02")

    class EmptyManager:
        def list_incidents(self):
            return []

        def list_problems(self):
            return []

        def list_changes(self):
            return []

        def search_kedb(self, _query):
            return []

    monkeypatch.setattr("skdashboard.dashboard_itil._mgr", lambda _home: EmptyManager())
    monkeypatch.setattr("skdashboard.dashboard_itil._now", lambda: now)
    economy = get_ai_usage(tmp_path, {"lane": "gateway_observed"}, now=now)
    reliability = get_reliability_projection(tmp_path, {})

    request = type(
        "Request",
        (),
        {
            "query_params": {},
            "state": type("State", (), {"gateway_role": "viewer", "gateway_scope": "fleet"})(),
        },
    )()
    fleet = project(
        [
            {
                "observed_at": observed,
                "payload_hash": "a" * 64,
                "facts": {
                    "breakdowns": {"models": [], "nodes": ["chiap01"]},
                    "gateway": {
                        "backend_health": {},
                        "expected_nodes": ["chiap01", "chiap02"],
                        "nodes": {"chiap01": {"configuration_drift": "clean"}},
                    },
                },
            }
        ],
        parse_query(request),
        timeseries=False,
    )

    assert reliability["node_coverage"] == economy["coverage"]
    assert fleet["node_totals"] == {
        "named": economy["coverage"]["expected_nodes"],
        "current": economy["coverage"]["fresh_collectors"],
        "stale": economy["coverage"]["stale_collectors"],
        "missing": len(economy["coverage"]["missing_nodes"]),
        "unknown": 0,
    }


def test_collectors_are_ordered_by_freshness_then_identity(data_root, tmp_path):
    _write(data_root, "stale.json", _snapshot(node="chiap01", observed="2026-08-20T12:00:00Z"))
    _write(data_root, "delayed.json", _snapshot(node="chiap04", observed="2026-08-23T00:00:00Z"))
    _write(data_root, "fresh.json", _snapshot(node="chiap08", observed="2026-08-23T11:50:00Z"))

    result = get_ai_usage(
        tmp_path,
        now=datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
    )

    assert [(row["node_id"], row["status"]) for row in result["collectors"]] == [
        ("chiap08", "fresh"),
        ("chiap04", "delayed"),
        ("chiap01", "stale"),
    ]


class _FakeRequest:
    headers = {}
    path_params = {}

    def __init__(self, query_params=None):
        self.query_params = query_params or {}


def _route_endpoint(app, path):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"no route registered for path {path!r}")


def test_economy_route_includes_ai_usage_and_accepts_filters(data_root, tmp_path):
    _write(data_root, "usage.json", _snapshot())
    app = create_app(tmp_path)
    handler = _route_endpoint(app, "/api/economy")

    response = asyncio.run(handler(_FakeRequest({"client": "codex"})))
    document = json.loads(response.body)

    assert response.status_code == 200
    assert document["ai_usage"]["summary"]["tokens"]["total"] == 100
    assert document["ai_usage"]["filters"]["client"] == "codex"
    assert any(getattr(route, "path", None) == "/economy" for route in app.routes)


def test_economy_page_separates_usage_autopilot_and_joule():
    page = (
        Path(__file__).parents[1]
        / "src"
        / "skdashboard"
        / "static"
        / "economy.html"
    ).read_text(encoding="utf-8")

    assert 'id="eco-ai-usage"' in page
    assert 'id="eco-autopilot"' in page
    assert 'id="eco-joule"' in page
    assert "One measurement lane at a time" in page
    assert "No implicit token or USD conversion" in page
    assert "Provider quota connectors are not configured" in page
