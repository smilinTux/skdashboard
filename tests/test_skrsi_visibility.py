from __future__ import annotations

import json
import threading

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
    changed_revision = visibility.project(
        "trends", [], target_revision="v2", cohort="all"
    )
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
    assert (
        client.get("/api/visibility/trends?role=auditor&matter_id=x").status_code == 400
    )
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
    assert "Provider evidence failed closed" in script.text


def test_provider_timeout_and_overload_fail_closed_with_redacted_evidence(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    notifications = []

    def provider(_kind):
        entered.set()
        release.wait(1)
        return supplied_projection()

    client = TestClient(
        create_app(
            tmp_path,
            visibility_provider=provider,
            visibility_authorizer=lambda _request, _role: True,
            visibility_notifier=notifications.append,
            visibility_timeout_seconds=0.02,
            visibility_max_concurrency=1,
            visibility_max_queue=0,
            visibility_retries=0,
        )
    )
    timed_out = client.get("/api/visibility/trends?role=viewer")
    assert entered.is_set()
    overloaded = client.get("/api/visibility/trends?role=viewer")
    release.set()

    assert timed_out.status_code == overloaded.status_code == 503
    assert timed_out.json()["error"] == "visibility_provider_timeout"
    assert overloaded.json()["error"] == "visibility_provider_overloaded"
    assert all(item["escalation"] == "notification_only" for item in notifications)
    records = [
        json.loads(line)
        for line in (tmp_path / "evidence" / "skrsi-visibility" / "terminal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["code"] for record in records] == [
        "visibility_provider_timeout",
        "visibility_provider_overloaded",
    ]
    assert "secret" not in json.dumps(records).lower()


def test_provider_retry_replay_stale_and_malformed_fail_closed(tmp_path):
    attempts = 0

    def retry_provider(_kind):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("secret upstream detail")
        return supplied_projection()

    client = TestClient(
        create_app(
            tmp_path,
            visibility_provider=retry_provider,
            visibility_authorizer=lambda _request, _role: True,
            visibility_retries=1,
        )
    )
    assert client.get("/api/visibility/trends?role=viewer").status_code == 200
    assert attempts == 2

    outcomes = iter(({"freshness": "stale", "secret": "hidden"}, []))
    replay = TestClient(
        create_app(
            tmp_path,
            visibility_provider=lambda _kind: next(outcomes),
            visibility_authorizer=lambda _request, _role: True,
            visibility_retries=0,
        )
    )
    stale = replay.get("/api/visibility/trends?role=viewer")
    malformed = replay.get("/api/visibility/trends?role=viewer")
    assert stale.status_code == 503
    assert stale.json()["error"] == "visibility_provider_stale"
    assert malformed.status_code == 422
    assert malformed.json()["error"] == "visibility_provider_malformed"
    assert "hidden" not in stale.text
    assert "hidden" not in malformed.text


def test_contract_malformed_mappings_emit_one_redacted_notification_and_record(
    tmp_path,
):
    notifications = []
    outcomes = []
    for protected in ("row-protected-value", "metadata-protected-value"):
        supplied = supplied_projection()
        if protected.startswith("row"):
            supplied["rows"] = [{"metric": "throughput", "details": protected}]
        else:
            supplied["missingness"] = {protected: "not-an-integer"}
        outcomes.append(supplied)

    client = TestClient(
        create_app(
            tmp_path,
            visibility_provider=lambda _kind: outcomes.pop(0),
            visibility_authorizer=lambda _request, _role: True,
            visibility_notifier=notifications.append,
        )
    )
    responses = [
        client.get("/api/visibility/trends?role=viewer"),
        client.get("/api/visibility/trends?role=viewer"),
    ]
    assert all(response.status_code == 422 for response in responses)
    assert all(
        response.json()["error"] == "visibility_provider_malformed"
        for response in responses
    )
    records = [
        json.loads(line)
        for line in (tmp_path / "evidence" / "skrsi-visibility" / "terminal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == len(notifications) == 2
    assert all(record["code"] == "visibility_provider_malformed" for record in records)
    assert all(item["escalation"] == "notification_only" for item in notifications)
    serialized = json.dumps(
        {
            "responses": [item.json() for item in responses],
            "records": records,
            "notifications": notifications,
        }
    )
    assert "row-protected-value" not in serialized
    assert "metadata-protected-value" not in serialized
