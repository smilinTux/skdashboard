from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

from starlette.testclient import TestClient
from test_control_plane_decision_context import ORIGIN, Rig

from skdashboard.dashboard import create_app


class _Routes:
    def __init__(self, *rigs: Rig):
        self.rigs = {rig.binding.target: rig for rig in rigs}

    def authorize_with_currentness(self, bearer, invocation):
        return self.rigs[invocation.target].authorizer.authorize_with_currentness(
            bearer, invocation
        )

    def factory(self, request, capability, target):
        return self.rigs[target].factory(request, capability, target)


PATH = "/api/v1/reports/projection?role=project-manager&scope=estate&window=latest&baseline=none&service=all&report_type=all"


def snapshot():
    return {
        "snapshot_id": "rpt-example-report",
        "schema_version": "1.1.0",
        "report_hash": "sha256:" + "a" * 64,
    }


def projection(query):
    return {
        "schema_version": "1.0.0",
        "projection_id": "reports-latest",
        "scope": query,
        "truth_state": "unknown",
        "reports": [],
        "selected": None,
        "comparison": None,
        "errors": [],
    }


def test_report_provider_receives_exact_context_scope_and_verifier(tmp_path: Path):
    rig = Rig(target="/api/v1/reports/projection")
    calls = []

    class Provider:
        def read(self, context, query, home, *, currentness_verifier):
            assert context.binding == rig.binding
            assert home == tmp_path
            assert currentness_verifier.check_before_owner_read(context).value == "allow"
            calls.append(dict(query))
            assert currentness_verifier.check_after_owner_read(context).value == "allow"
            return projection(query)

    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=rig.authorizer,
        control_plane_invocation_factory=rig.factory,
        control_plane_report_provider=Provider(),
    )
    response = TestClient(app).get(
        PATH,
        headers={"Authorization": f"Bearer {rig.bearer}", "Origin": ORIGIN},
    )
    assert response.status_code == 200
    assert calls == [
        {
            "role": "project-manager",
            "scope": "estate",
            "window": "latest",
            "baseline": "none",
            "service": "all",
            "report_type": "all",
        }
    ]
    assert response.headers["etag"]


def test_report_detail_is_get_only_etagged_and_uses_provider(tmp_path: Path):
    rig = Rig(
        target="/api/v1/reports/rpt-example-report",
        capability="skdashboard.reports.read",
    )

    class Provider:
        def read_snapshot(self, context, snapshot_id, home, *, currentness_verifier):
            assert context.binding == rig.binding
            assert snapshot_id == "rpt-example-report"
            assert home == tmp_path
            assert currentness_verifier.check_before_owner_read(context).value == "allow"
            assert currentness_verifier.check_after_owner_read(context).value == "allow"
            return snapshot()

    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=rig.authorizer,
        control_plane_invocation_factory=rig.factory,
        control_plane_report_provider=Provider(),
    )
    headers = {"Authorization": f"Bearer {rig.bearer}", "Origin": ORIGIN}
    response = TestClient(app).get("/api/v1/reports/rpt-example-report", headers=headers)
    assert response.status_code == 200
    assert response.json()["snapshot_id"] == "rpt-example-report"
    assert response.headers["etag"] == '"' + "a" * 64 + '"'
    assert (
        TestClient(app).post("/api/v1/reports/rpt-example-report", headers=headers).status_code
        == 405
    )


def test_report_scope_rejects_unknown_duplicate_and_protected_values(tmp_path: Path):
    class Provider:
        def read(self, *_args, **_kwargs):
            raise AssertionError("provider must not run")

    for suffix in ("&unknown=value", "&role=operator", "&tenant_id=protected"):
        rig = Rig(target="/api/v1/reports/projection")
        app = create_app(
            tmp_path,
            control_plane_decision_authorizer=rig.authorizer,
            control_plane_invocation_factory=rig.factory,
            control_plane_report_provider=Provider(),
        )
        response = TestClient(app).get(
            PATH + suffix,
            headers={"Authorization": f"Bearer {rig.bearer}", "Origin": ORIGIN},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_REPORT_SCOPE"


def test_slow_report_does_not_starve_health_evidence_or_common_reads(tmp_path: Path):
    rig = Rig(target="/api/v1/reports/projection")
    entered = Event()
    release = Event()

    class Provider:
        def read(self, _context, query, _home, *, currentness_verifier):
            entered.set()
            release.wait(2)
            return projection(query)

    overview = Rig(target="/api/v1/overview")
    board = Rig(target="/api/v1/board/summary")
    metrics_rig = Rig(target="/metrics")
    routes = _Routes(rig, overview, board, metrics_rig)
    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=routes,
        control_plane_invocation_factory=routes.factory,
        control_plane_report_provider=Provider(),
    )
    result = {}

    def report():
        result["response"] = TestClient(app).get(
            PATH,
            headers={"Authorization": f"Bearer {rig.fresh_bearer()}", "Origin": ORIGIN},
        )

    thread = Thread(target=report)
    thread.start()
    assert entered.wait(1)
    client = TestClient(app)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get(
        "/api/v1/overview",
        headers={"Authorization": f"Bearer {overview.fresh_bearer()}", "Origin": ORIGIN},
    ).status_code == 200
    assert client.get(
        "/api/v1/board/summary",
        headers={"Authorization": f"Bearer {board.fresh_bearer()}", "Origin": ORIGIN},
    ).status_code == 200
    metrics = client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {metrics_rig.fresh_bearer()}", "Origin": ORIGIN},
    )
    assert metrics.status_code == 200
    assert 'skdashboard_workload_active{workload="report"} 1' in metrics.text
    release.set()
    thread.join(2)
    assert result["response"].status_code == 200


def test_report_worker_failure_is_bounded_unavailable(tmp_path: Path):
    rig = Rig(target="/api/v1/reports/projection")

    class Provider:
        def read(self, *_args, **_kwargs):
            raise RuntimeError("public-synthetic worker outage")

    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=rig.authorizer,
        control_plane_invocation_factory=rig.factory,
        control_plane_report_provider=Provider(),
    )
    response = TestClient(app).get(
        PATH,
        headers={"Authorization": f"Bearer {rig.bearer}", "Origin": ORIGIN},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "REPORTS_UNAVAILABLE"
    assert response.json()["retryable"] is True
    assert "public-synthetic" not in response.text


def test_report_provider_requires_typed_authorization(tmp_path: Path):
    try:
        create_app(tmp_path, control_plane_report_provider=object())
    except ValueError as error:
        assert "typed control-plane authorization" in str(error)
    else:
        raise AssertionError("report provider must fail closed without typed authorization")
