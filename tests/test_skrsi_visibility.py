from __future__ import annotations

from starlette.testclient import TestClient

from skdashboard import visibility
from skdashboard.dashboard import create_app


def supplied_projection():
    return {
        "target_revision": "target-v7",
        "cohort": "canary",
        "freshness": "current",
        "missingness": {"latency_ms": 1},
        "evaluator_version": "evaluator-v3",
        "rows": [
            {
                "target_id": "queue-handoff",
                "metric": "latency_ms",
                "value": 12.5,
                "unit": "ms",
                "sample_size": 24,
            }
        ],
    }


def test_every_view_returns_complete_evidence_bound_metadata(tmp_path):
    requested = []

    def provider(kind):
        requested.append(kind)
        return supplied_projection()

    client = TestClient(
        create_app(
            tmp_path,
            visibility_provider=provider,
            visibility_authorizer=lambda _request, _role: True,
        )
    )
    for kind in sorted(visibility.VIEWS):
        response = client.get(f"/api/visibility/{kind}?role=viewer")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == kind
        assert body["target_revision"] == "target-v7"
        assert body["cohort"] == "canary"
        assert body["freshness"] == "current"
        assert body["missingness"] == {"latency_ms": 1}
        assert body["sample_size"] == 1
        assert body["evaluator_version"] == "evaluator-v3"
        assert body["evidence_hash"].startswith("sha256:")
    assert requested == sorted(visibility.VIEWS)


def test_evidence_hash_binds_metadata_and_rows():
    baseline = visibility.project("trends", [], target_revision="v1", cohort="all")
    changed_revision = visibility.project("trends", [], target_revision="v2", cohort="all")
    changed_rows = visibility.project(
        "trends",
        [{"metric": "throughput", "value": 2}],
        target_revision="v1",
        cohort="all",
    )
    assert (
        len(
            {
                baseline["evidence_hash"],
                changed_revision["evidence_hash"],
                changed_rows["evidence_hash"],
            }
        )
        == 3
    )


def test_http_authorization_and_scope_fail_closed(tmp_path):
    provider_calls = []

    def provider(kind):
        provider_calls.append(kind)
        return supplied_projection()

    app = create_app(
        tmp_path,
        visibility_provider=provider,
        visibility_authorizer=lambda _request, role: role == "auditor",
    )
    client = TestClient(app)
    assert client.get("/api/visibility/trends?role=viewer").status_code == 403
    assert client.get("/api/visibility/trends?role=owner").status_code == 403
    assert client.get("/api/visibility/trends?role=auditor&matter_id=x").status_code == 400
    assert client.get("/api/visibility/not-a-view?role=auditor").status_code == 404
    assert provider_calls == []
    assert client.get("/api/visibility/trends?role=auditor").status_code == 200
    assert provider_calls == ["trends"]


def test_http_requires_authorizer_before_provider_access(tmp_path):
    provider_calls = []

    def provider(kind):
        provider_calls.append(kind)
        return supplied_projection()

    response = TestClient(create_app(tmp_path, visibility_provider=provider)).get(
        "/api/visibility/trends?role=viewer"
    )
    assert response.status_code == 403
    assert provider_calls == []


def test_protected_or_unbounded_payloads_never_serialize(tmp_path):
    protected_fields = (
        "secret",
        "prompt",
        "response",
        "matter_id",
        "inbox_body",
        "corpus_text",
        "token",
        "credential",
    )
    for field in protected_fields:
        supplied = supplied_projection()
        supplied["rows"] = [{"metric": "throughput", field: "protected-value"}]
        client = TestClient(
            create_app(
                tmp_path,
                visibility_provider=lambda _kind, s=supplied: s,
                visibility_authorizer=lambda _request, _role: True,
            )
        )
        response = client.get("/api/visibility/trends?role=viewer")
        assert response.status_code == 422
        assert "protected-value" not in response.text

    supplied = supplied_projection()
    supplied["rows"] = [{"metric": "throughput", "details": "arbitrary payload"}]
    response = TestClient(
        create_app(
            tmp_path,
            visibility_provider=lambda _kind: supplied,
            visibility_authorizer=lambda _request, _role: True,
        )
    ).get("/api/visibility/trends?role=viewer")
    assert response.status_code == 422
    assert "arbitrary payload" not in response.text


def test_overview_renders_responsive_visibility_widgets(tmp_path):
    client = TestClient(create_app(tmp_path))
    page = client.get("/")
    script = client.get("/static/js/overview.js")
    assert page.status_code == 200
    assert 'id="visibility-grid"' in page.text
    assert 'class="cbody ov-grid"' in page.text
    assert script.status_code == 200
    assert "/api/visibility/" in script.text
    assert "evidence_hash" in script.text
