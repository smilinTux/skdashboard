from __future__ import annotations

import copy
from pathlib import Path
from threading import Event, Thread

from starlette.testclient import TestClient
from test_control_plane_decision_context import ORIGIN, Rig

from skdashboard.control_plane_fixture import INSIGHT, SCOPE, WINDOW
from skdashboard.dashboard import create_app

PATH = "/api/v1/insights/query"
QUERY = {
    "question": "Summarize the public synthetic portfolio evidence.",
    "scope": SCOPE,
    "window": WINDOW,
    "intent": "brief",
    "metric_families": ["portfolio"],
    "baseline": None,
}


def _headers(rig: Rig) -> dict[str, str]:
    return {"Authorization": f"Bearer {rig.fresh_bearer()}", "Origin": ORIGIN}


class _Routes:
    def __init__(self, *rigs: Rig):
        self.rigs = {rig.binding.target: rig for rig in rigs}

    def authorize_with_currentness(self, bearer, invocation):
        return self.rigs[invocation.target].authorizer.authorize_with_currentness(
            bearer, invocation
        )

    def factory(self, request, capability, target):
        return self.rigs[target].factory(request, capability, target)


def test_governed_insight_is_typed_bounded_and_provider_authorized(tmp_path: Path):
    rig = Rig(target=PATH, capability="skdashboard.insights.query")
    calls = []

    class Provider:
        def read(self, context, query, home, *, currentness_verifier):
            assert context.binding == rig.binding
            assert home == tmp_path
            assert currentness_verifier.check_before_owner_read(context).value == "allow"
            calls.append(copy.deepcopy(query))
            assert currentness_verifier.check_after_owner_read(context).value == "allow"
            return copy.deepcopy(INSIGHT)

    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=rig.authorizer,
        control_plane_invocation_factory=rig.factory,
        control_plane_insight_provider=Provider(),
    )
    response = TestClient(app).post(PATH, headers=_headers(rig), json=QUERY)
    assert response.status_code == 200
    assert response.json() == INSIGHT
    assert response.headers["etag"]
    assert calls == [QUERY]
    assert TestClient(app).get(PATH, headers=_headers(rig)).status_code == 405


def test_insight_rejects_invalid_oversized_and_unavailable_inputs(tmp_path: Path):
    rig = Rig(target=PATH, capability="skdashboard.insights.query")

    class Provider:
        def read(self, *_args, **_kwargs):
            raise AssertionError("invalid query must not run the provider")

    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=rig.authorizer,
        control_plane_invocation_factory=rig.factory,
        control_plane_insight_provider=Provider(),
    )
    client = TestClient(app)
    for invalid in (
        {**QUERY, "matter_id": "protected"},
        {**QUERY, "scope": {"matter_id": "protected"}},
        {**QUERY, "metric_families": ["individual-ranking"]},
        {**QUERY, "question": ""},
    ):
        response = client.post(PATH, headers=_headers(rig), json=invalid)
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_INSIGHT_QUERY"
    oversized = client.post(
        PATH,
        headers={**_headers(rig), "Content-Type": "application/json"},
        content=b"x" * (64 * 1024 + 1),
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "REQUEST_TOO_LARGE"

    unavailable = create_app(
        tmp_path,
        control_plane_decision_authorizer=rig.authorizer,
        control_plane_invocation_factory=rig.factory,
    )
    response = TestClient(unavailable).post(PATH, headers=_headers(rig), json=QUERY)
    assert response.status_code == 503
    assert response.json()["code"] == "INSIGHTS_UNAVAILABLE"


def test_slow_insight_does_not_starve_health_board_or_metrics(tmp_path: Path):
    rig = Rig(target=PATH, capability="skdashboard.insights.query")
    entered = Event()
    release = Event()

    class Provider:
        def read(self, _context, _query, _home, *, currentness_verifier):
            entered.set()
            release.wait(2)
            return copy.deepcopy(INSIGHT)

    overview = Rig(target="/api/v1/overview")
    board = Rig(target="/api/v1/board/summary")
    metrics_rig = Rig(target="/metrics")
    routes = _Routes(rig, overview, board, metrics_rig)
    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=routes,
        control_plane_invocation_factory=routes.factory,
        control_plane_insight_provider=Provider(),
    )
    result = {}

    def query():
        result["response"] = TestClient(app).post(PATH, headers=_headers(rig), json=QUERY)

    thread = Thread(target=query)
    thread.start()
    assert entered.wait(1)
    client = TestClient(app)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/overview", headers=_headers(overview)).status_code == 200
    assert client.get("/api/v1/board/summary", headers=_headers(board)).status_code == 200
    metrics = client.get("/metrics", headers=_headers(metrics_rig))
    assert metrics.status_code == 200
    assert 'skdashboard_workload_active{workload="insight"} 1' in metrics.text
    assert 'skdashboard_workload_active{workload="report"} 0' in metrics.text
    release.set()
    thread.join(2)
    assert result["response"].status_code == 200
