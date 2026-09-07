from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from skdashboard.dashboard import create_app
from skdashboard.dashboard_itil import _gateway_snapshot, get_overview, get_reliability_projection

ROOT = Path(__file__).parents[1]
NOW = datetime.now(timezone.utc)


class Value:
    def __init__(self, value):
        self.value = value


class Record:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, **_kwargs):
        def plain(value):
            if isinstance(value, Value):
                return value.value
            if isinstance(value, list):
                return [plain(item) for item in value]
            if isinstance(value, dict):
                return {key: plain(item) for key, item in value.items()}
            return value

        return plain(self.__dict__)


def incident(index, *, minutes=30, status="detected", problem=None):
    detected = NOW - timedelta(minutes=minutes)
    return Record(
        id=f"inc-{index}",
        type="incident",
        title=f"Incident {index}",
        status=Value(status),
        severity=Value("sev2"),
        affected_services=["api"],
        related_problem_id=problem,
        detected_at=detected.isoformat(),
        acknowledged_at=(detected + timedelta(minutes=5)).isoformat(),
        resolved_at=(detected + timedelta(minutes=20)).isoformat()
        if status == "resolved"
        else None,
        timeline=[],
    )


def change(identifier, status, timeline):
    return Record(
        id=identifier,
        type="change",
        title=identifier,
        status=Value(status),
        change_type=Value("normal"),
        risk=Value("medium"),
        timeline=timeline,
        created_at=(NOW - timedelta(hours=2)).isoformat(),
        related_problem_id="prb-1",
        validation={"passed": True},
        cab_required=True,
        scheduled_window={"window_start": NOW.isoformat()},
        rollback_plan="restore prior release",
    )


class Manager:
    def __init__(self):
        self.incidents = [incident(index, minutes=120 + index) for index in range(10)]
        self.problems = [
            Record(
                id="prb-1",
                title="Recurring API",
                status=Value("known_error"),
                related_incident_ids=["inc-1", "inc-2"],
                kedb_id="ke-1",
                related_change_id="chg-ok",
                workaround="restart",
                timeline=[],
            )
        ]
        self.changes = [
            change(
                "chg-ok",
                "closed",
                [
                    {
                        "ts": (NOW - timedelta(minutes=30)).isoformat(),
                        "action": "status:implementing->deployed",
                    },
                    {
                        "ts": (NOW - timedelta(minutes=20)).isoformat(),
                        "action": "status:deployed->verified",
                        "note": "PIR passed",
                    },
                    {"ts": NOW.isoformat(), "action": "status:verified->closed"},
                ],
            ),
            change(
                "chg-failed",
                "closed",
                [
                    {
                        "ts": NOW.isoformat(),
                        "action": "status:failed->closed",
                        "note": "rollback complete",
                    }
                ],
            ),
            change("chg-rejected", "rejected", []),
            change(
                "chg-deployed",
                "deployed",
                [{"ts": NOW.isoformat(), "action": "status:implementing->deployed"}],
            ),
        ]
        self.kedb = [
            Record(
                id="ke-1",
                title="API known error",
                related_problem_id="prb-1",
                permanent_fix_change_id="chg-ok",
                root_cause="known",
                workaround="restart",
                symptoms=[],
            )
        ]

    def list_incidents(self):
        return self.incidents

    def list_problems(self):
        return self.problems

    def list_changes(self):
        return self.changes

    def search_kedb(self, _query):
        return self.kedb

    def get_cab_votes(self, _identifier):
        return [Record(decision=Value("approved"), agent="human")]


def write_gateway_snapshot(home: Path, **updates) -> dict:
    document = {
        "schema_version": "1.0.0",
        "source": "skgateway",
        "observed_at": NOW.isoformat(),
        "window": "60s",
        "sample_size": 11,
        "observations": {
            "success": 10, "error": 1, "rate_limited_429": 1, "timeouts": 1,
            "latency_percentiles": {"p50": 12.5, "p95": 40, "p99": 80},
            "slo_state": "at_risk", "rollback_triggers": 0,
        },
    }
    document.update(updates)
    document["snapshot_hash"] = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target = home / "skgateway" / "reliability.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    target.chmod(0o644)
    return document


def test_reliability_metrics_use_full_denominators_and_terminal_change_outcomes(
    monkeypatch,
) -> None:
    manager = Manager()
    monkeypatch.setattr("skdashboard.dashboard_itil._mgr", lambda _home: manager)
    projection = get_reliability_projection(
        Path("/unused"),
        {
            "role": "operator",
            "scope": "estate",
            "window": "latest",
            "baseline": "none",
            "service": "all",
        },
    )
    metrics = {item["metric_id"]: item for item in projection["metrics"]}

    assert len(projection["items"]["breach_risk"]) == 8
    assert metrics["itil.open_sla_breaches"]["numerator"] == 10
    assert metrics["itil.open_sla_breaches"]["denominator"] == 10
    assert metrics["itil.change_success_rate"]["numerator"] == 1
    assert metrics["itil.change_success_rate"]["denominator"] == 2
    assert metrics["itil.change_success_rate"]["value"] == 50.0
    assert [item["outcome"] for item in projection["items"]["changes"]] == [
        "successful",
        "failed",
        "rejected",
        "pending",
    ]
    assert metrics["service.slo_target"]["truth_state"] == "unknown"
    for item in projection["metrics"]:
        assert {
            "numerator",
            "denominator",
            "sample_size",
            "window",
            "classification",
            "exclusions",
            "legacy_coverage",
        } <= item.keys()

    overview = get_overview(Path("/unused"))
    assert overview["kpis"]["change_success"] == 50
    assert overview["kpis"]["change_fail"] == 50


def test_empty_reliability_source_is_unknown_not_zero(monkeypatch) -> None:
    manager = Manager()
    manager.incidents = []
    manager.problems = []
    manager.changes = []
    manager.kedb = []
    monkeypatch.setattr("skdashboard.dashboard_itil._mgr", lambda _home: manager)
    projection = get_reliability_projection(
        Path("/unused"),
        {
            "role": "operator",
            "scope": "estate",
            "window": "latest",
            "baseline": "none",
            "service": "all",
        },
    )
    assert projection["truth_state"] == "unknown"
    assert all(
        item["truth_state"] == "unknown" and item["value"] is None
        for item in projection["metrics"]
    )


def test_gateway_snapshot_is_digest_bound_typed_and_current(tmp_path: Path) -> None:
    document = write_gateway_snapshot(tmp_path)
    snapshot = _gateway_snapshot(tmp_path, now=NOW + timedelta(seconds=30))
    assert snapshot is not None
    assert snapshot["snapshot_hash"] == document["snapshot_hash"]
    assert snapshot["age_seconds"] == 30


def test_gateway_snapshot_rejects_tampering_staleness_and_untyped_values(tmp_path: Path) -> None:
    document = write_gateway_snapshot(tmp_path)
    target = tmp_path / "skgateway" / "reliability.json"
    document["observations"]["success"] = 999
    target.write_text(json.dumps(document), encoding="utf-8")
    target.chmod(0o644)
    assert _gateway_snapshot(tmp_path, now=NOW) is None

    write_gateway_snapshot(tmp_path, observed_at=(NOW - timedelta(seconds=121)).isoformat())
    assert _gateway_snapshot(tmp_path, now=NOW) is None
    valid_observations = write_gateway_snapshot(tmp_path)["observations"]
    for updates in (
        {"sample_size": True},
        {"observations": {"success": "10"}},
        {"observations": {**valid_observations, "slo_state": "probably-fine"}},
    ):
        write_gateway_snapshot(tmp_path, **updates)
        assert _gateway_snapshot(tmp_path, now=NOW) is None


def test_gateway_metrics_remain_separate_and_missing_never_becomes_zero(
    tmp_path: Path, monkeypatch
) -> None:
    manager = Manager()
    manager.incidents = []
    manager.problems = []
    manager.changes = []
    manager.kedb = []
    monkeypatch.setattr("skdashboard.dashboard_itil._mgr", lambda _home: manager)
    monkeypatch.setattr("skdashboard.dashboard_itil._now", lambda: NOW)
    document = write_gateway_snapshot(tmp_path)
    projection = get_reliability_projection(tmp_path, {"scope": "estate"})
    metrics = {item["metric_id"]: item for item in projection["metrics"]}
    assert projection["truth_state"] == "current"
    assert projection["source_watermarks"] == [
        {"source": "skcoord.itil", "value": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},
        {"source": "skgateway.observed", "value": f"skgateway.observed:{document['snapshot_hash']}"},
    ]
    assert metrics["gateway.request_success"]["value"] == 10
    assert metrics["gateway.latency_percentiles"]["value"] == {
        "p50": 12.5, "p95": 40, "p99": 80,
    }
    (tmp_path / "skgateway" / "reliability.json").unlink()
    unavailable = get_reliability_projection(tmp_path, {"scope": "estate"})
    gateway = [item for item in unavailable["metrics"]
               if item["metric_id"].startswith("gateway.")]
    assert all(item["value"] is None and item["truth_state"] == "unknown"
               for item in gateway)


def test_reliability_page_is_read_only_and_has_accessible_tables(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, control_plane_authorizer=lambda *_: False))
    response = client.get("/control-plane/reliability")
    assert response.status_code == 200
    assert "Service levels, change health, PIR, and KEDB" in response.text
    assert 'id="reliability-metric-rows"' in response.text
    assert 'id="reliability-lineage-rows"' in response.text
    assert client.post("/control-plane/reliability").status_code == 405


def test_reliability_api_fails_closed_without_typed_provider(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path, control_plane_authorizer=lambda *_: True)).get(
        "/api/v1/reliability/projection?role=operator&scope=estate&window=latest&baseline=none&service=all",
        headers={"Authorization": "Bearer legacy"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "RELIABILITY_UNAVAILABLE"


def test_reliability_surface_has_no_mutation_controls_or_external_dependencies() -> None:
    html = (ROOT / "src/skdashboard/static/reliability.html").read_text()
    js = (ROOT / "src/skdashboard/static/js/reliability.js").read_text()
    css = (ROOT / "src/skdashboard/static/css/reliability.css").read_text()
    for marker in (
        "No-write boundary",
        "Numerator",
        "Legacy coverage",
        "INC, PRB, and CHG values are provenance aliases",
    ):
        assert marker in html
    assert "/api/v1/reliability/projection" in js
    assert '["p50", "p95", "p99"]' in js
    assert '"SKGateway evidence"' in js
    assert "Object.hasOwn" in js
    assert "fetch(" not in js
    assert "postChange" not in js
    assert "@media(max-width:760px)" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
