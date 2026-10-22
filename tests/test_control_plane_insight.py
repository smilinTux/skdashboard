from __future__ import annotations

import time
from pathlib import Path

from starlette.testclient import TestClient
from test_control_plane_decision_context import ORIGIN, Rig

from skdashboard.control_plane_fixture import INSIGHT, SCOPE, WINDOW
from skdashboard.dashboard import create_app
from skdashboard.workload_isolation import BoundedWorkloadManager, WorkloadLimits


def query() -> dict:
    return {
        "question": "Summarize the public synthetic portfolio evidence.",
        "scope": SCOPE,
        "window": WINDOW,
        "intent": "brief",
        "metric_families": ["portfolio"],
        "baseline": None,
    }


def headers(rig: Rig) -> dict[str, str]:
    return {"Authorization": f"Bearer {rig.bearer}", "Origin": ORIGIN}


def test_insight_provider_is_typed_authorized_and_schema_validated(tmp_path: Path) -> None:
    rig = Rig(target="/api/v1/insights/query", capability="skdashboard.insights.query")
    calls = []

    class Provider:
        def query(self, context, submitted, home, *, currentness_verifier):
            assert context.binding == rig.binding
            assert submitted == query()
            assert home == tmp_path
            assert currentness_verifier.check_before_owner_read(context).value == "allow"
            calls.append(submitted)
            return INSIGHT

    manager = BoundedWorkloadManager()
    try:
        app = create_app(
            tmp_path,
            control_plane_decision_authorizer=rig.authorizer,
            control_plane_invocation_factory=rig.factory,
            control_plane_insight_provider=Provider(),
            control_plane_workload_manager=manager,
        )
        response = TestClient(app).post(
            "/api/v1/insights/query", headers=headers(rig), json=query()
        )
        assert response.status_code == 200
        assert response.json() == INSIGHT
        assert response.headers["etag"]
        assert calls == [query()]
    finally:
        manager.close()


def test_insight_query_rejects_invalid_schema_before_provider(tmp_path: Path) -> None:
    rig = Rig(target="/api/v1/insights/query", capability="skdashboard.insights.query")
    calls = []

    class Provider:
        def query(self, *_args, **_kwargs):
            calls.append(True)
            return INSIGHT

    manager = BoundedWorkloadManager()
    try:
        app = create_app(
            tmp_path,
            control_plane_decision_authorizer=rig.authorizer,
            control_plane_invocation_factory=rig.factory,
            control_plane_insight_provider=Provider(),
            control_plane_workload_manager=manager,
        )
        response = TestClient(app).post(
            "/api/v1/insights/query", headers=headers(rig), json={"question": "invalid"}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_QUERY"
        assert calls == []
    finally:
        manager.close()


def test_insight_provider_unavailable_fails_closed(tmp_path: Path) -> None:
    rig = Rig(target="/api/v1/insights/query", capability="skdashboard.insights.query")
    manager = BoundedWorkloadManager()
    try:
        app = create_app(
            tmp_path,
            control_plane_decision_authorizer=rig.authorizer,
            control_plane_invocation_factory=rig.factory,
            control_plane_workload_manager=manager,
        )
        response = TestClient(app).post(
            "/api/v1/insights/query", headers=headers(rig), json=query()
        )
        assert response.status_code == 503
        assert response.json()["code"] == "INSIGHTS_UNAVAILABLE"
    finally:
        manager.close()


def test_insight_timeout_is_bounded_and_retryable(tmp_path: Path) -> None:
    rig = Rig(target="/api/v1/insights/query", capability="skdashboard.insights.query")

    class Provider:
        def query(self, *_args, **_kwargs):
            time.sleep(0.05)
            return INSIGHT

    manager = BoundedWorkloadManager(
        {"insight": WorkloadLimits(max_concurrency=1, max_queue=1, timeout_seconds=0.01), "report": WorkloadLimits(1, 1, 1)}
    )
    try:
        app = create_app(
            tmp_path,
            control_plane_decision_authorizer=rig.authorizer,
            control_plane_invocation_factory=rig.factory,
            control_plane_insight_provider=Provider(),
            control_plane_workload_manager=manager,
        )
        response = TestClient(app).post(
            "/api/v1/insights/query", headers=headers(rig), json=query()
        )
        assert response.status_code == 503
        assert response.json()["code"] == "INSIGHT_WORKLOAD_TIMEOUT"
        assert response.json()["retryable"] is True
        time.sleep(0.06)
        assert manager.snapshot()["insight"]["pending"] == 0
    finally:
        manager.close()


def test_insight_provider_failure_fails_closed(tmp_path: Path) -> None:
    rig = Rig(target="/api/v1/insights/query", capability="skdashboard.insights.query")

    class Provider:
        def query(self, *_args, **_kwargs):
            raise RuntimeError("synthetic provider unavailable")

    manager = BoundedWorkloadManager()
    try:
        app = create_app(
            tmp_path,
            control_plane_decision_authorizer=rig.authorizer,
            control_plane_invocation_factory=rig.factory,
            control_plane_insight_provider=Provider(),
            control_plane_workload_manager=manager,
        )
        response = TestClient(app).post(
            "/api/v1/insights/query", headers=headers(rig), json=query()
        )
        assert response.status_code == 503
        assert response.json()["code"] == "INSIGHTS_UNAVAILABLE"
        assert manager.snapshot()["insight"]["failed"] == 1
    finally:
        manager.close()
