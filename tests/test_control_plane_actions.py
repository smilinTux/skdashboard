from __future__ import annotations

from capauth import ClientKind, RequestBoundary
from starlette.testclient import TestClient
from test_control_plane_decision_context import ORIGIN, Rig

from skdashboard.control_plane_actions import ActionPreviewStore, canonical_preview_hash
from skdashboard.dashboard import create_app


def _preview(*, action_class: str = "low_risk_internal", status: str = "ready") -> dict:
    return {
        "preview_id": "apv-action-preview-1",
        "schema_version": "1.1.0",
        "status": status,
        "action_class": action_class,
        "source_recommendation_id": "rec-action-1",
        "action_contract_id": "fixture.inspect",
        "action_contract_version": "1.0.0",
        "owner_service": "fixture-owner",
        "owner_operation": "inspect",
        "target": {"fixture": "one"},
        "expected_version": "v-1",
        "before_summary": "The fixture is unchanged.",
        "proposed_effect": "Apply one bounded internal action.",
        "blast_radius": "One fixture.",
        "risk": {"level": "low", "reasons": []},
        "reversibility": "automatic",
        "verification_plan": ["Read the owner receipt."],
        "rollback_plan": ["Use the owner rollback operation."],
        "required_scope": "fixture.action",
        "required_approvals": [],
        "policy_decision_ref": "policy://fixture/allow",
        "expires_at": "2099-01-01T00:00:00Z",
    }


class Provider:
    def __init__(self) -> None:
        self.preview_calls = []
        self.authorize_calls = []

    def preview(self, context, request, home, *, currentness_verifier):
        self.preview_calls.append((context, request, home, currentness_verifier))
        return _preview()

    def authorize(self, context, preview, request, home, *, currentness_verifier):
        self.authorize_calls.append((context, preview, request, home, currentness_verifier))
        assert preview["preview_hash"] == canonical_preview_hash(
            {key: value for key, value in preview.items() if key != "preview_hash"}
        )
        return None


def _app(tmp_path, provider, store, preview_rig, authorize_rig):
    class DualAuthorizer:
        def authorize_with_currentness(self, bearer, invocation):
            rig = (
                preview_rig
                if invocation.capability == "skdashboard.actions.preview"
                else authorize_rig
            )
            return rig.authorizer.authorize_with_currentness(bearer, invocation)

    def factory(request, capability, target):
        rig = preview_rig if capability == "skdashboard.actions.preview" else authorize_rig
        invocation = rig.factory(request, capability, target)
        if capability == "skdashboard.actions.authorize":
            invocation = invocation.model_copy(
                update={
                    "boundary": RequestBoundary(
                        client_kind=ClientKind.BROWSER,
                        origin=ORIGIN,
                        csrf_verified=True,
                        idempotency_key=request.headers.get("idempotency-key"),
                    )
                }
            )
        return invocation

    return create_app(
        tmp_path,
        control_plane_decision_authorizer=DualAuthorizer(),
        control_plane_invocation_factory=factory,
        control_plane_action_provider=provider,
        control_plane_action_store=store,
    ), authorize_rig


def _headers(rig, *, fresh: bool = False, idempotency_key: str | None = None):
    headers = {
        "Authorization": f"Bearer {rig.fresh_bearer() if fresh else rig.bearer}",
        "Origin": ORIGIN,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def test_preview_is_hashed_stored_and_never_authorizes(tmp_path):
    preview_rig = Rig(
        target="/api/v1/actions/preview",
        capability="skdashboard.actions.preview",
        resource_id="actions",
    )
    authorize_rig = Rig(
        target="/api/v1/action-previews/apv-action-preview-1/authorize",
        capability="skdashboard.actions.authorize",
        resource_id="actions",
    )
    provider = Provider()
    store = ActionPreviewStore()
    app, _ = _app(tmp_path, provider, store, preview_rig, authorize_rig)

    response = TestClient(app).post(
        "/api/v1/actions/preview",
        json={
            "recommendation_id": "rec-action-1",
            "action_contract_id": "fixture.inspect",
            "parameter_proposal_ref": "proposal://action-1",
        },
        headers=_headers(preview_rig),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preview_hash"].startswith("sha256:")
    assert store.get_preview(body["preview_id"]) == body
    assert provider.preview_calls
    assert provider.authorize_calls == []
    assert all(
        "authorization" not in str(value).lower()
        for value in (body["proposed_effect"], body["verification_plan"])
    )


def test_authorization_revalidates_exact_hash_and_is_idempotent(tmp_path):
    preview_rig = Rig(
        target="/api/v1/actions/preview",
        capability="skdashboard.actions.preview",
        resource_id="actions",
    )
    authorize_rig = Rig(
        target="/api/v1/action-previews/apv-action-preview-1/authorize",
        capability="skdashboard.actions.authorize",
        resource_id="actions",
    )
    provider = Provider()
    store = ActionPreviewStore()
    app, _ = _app(tmp_path, provider, store, preview_rig, authorize_rig)
    client = TestClient(app)
    preview_response = client.post(
        "/api/v1/actions/preview",
        json={
            "recommendation_id": "rec-action-1",
            "action_contract_id": "fixture.inspect",
            "parameter_proposal_ref": "proposal://action-1",
        },
        headers=_headers(preview_rig),
    )
    preview = preview_response.json()
    request = {
        "preview_hash": preview["preview_hash"],
        "idempotency_key": "idem-action-preview-1",
        "approval_reason": "Approved bounded fixture action.",
    }
    path = "/api/v1/action-previews/apv-action-preview-1/authorize"

    first = client.post(
        path,
        json=request,
        headers=_headers(authorize_rig, fresh=True, idempotency_key=request["idempotency_key"]),
    )
    second = client.post(
        path,
        json=request,
        headers=_headers(authorize_rig, fresh=True, idempotency_key=request["idempotency_key"]),
    )

    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert second.status_code == 202
    assert second.json()["status"] == "duplicate"
    assert len(provider.authorize_calls) == 1
    assert [event["kind"] for event in store.events()] == [
        "preview_created",
        "receipt_recorded",
    ]


def test_authorization_rejects_hash_mismatch_before_owner_call(tmp_path):
    preview_rig = Rig(
        target="/api/v1/actions/preview",
        capability="skdashboard.actions.preview",
        resource_id="actions",
    )
    authorize_rig = Rig(
        target="/api/v1/action-previews/apv-action-preview-1/authorize",
        capability="skdashboard.actions.authorize",
        resource_id="actions",
    )
    provider = Provider()
    store = ActionPreviewStore()
    app, _ = _app(tmp_path, provider, store, preview_rig, authorize_rig)
    client = TestClient(app)
    preview_response = client.post(
        "/api/v1/actions/preview",
        json={
            "recommendation_id": "rec-action-1",
            "action_contract_id": "fixture.inspect",
            "parameter_proposal_ref": "proposal://action-1",
        },
        headers=_headers(preview_rig),
    )
    assert preview_response.status_code == 200

    response = client.post(
        "/api/v1/action-previews/apv-action-preview-1/authorize",
        json={
            "preview_hash": "sha256:" + "0" * 64,
            "idempotency_key": "idem-action-preview-2",
            "approval_reason": "Wrong exact version.",
        },
        headers=_headers(authorize_rig, fresh=True, idempotency_key="idem-action-preview-2"),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ACTION_PREVIEW_CONFLICT"
    assert provider.authorize_calls == []


def test_store_rejects_preview_rewrite_and_preserves_event_history():
    store = ActionPreviewStore()
    first = _preview()
    store.put_preview(first)
    changed = dict(first)
    changed["before_summary"] = "A different state."

    try:
        store.put_preview(changed)
    except ValueError:
        pass
    else:
        raise AssertionError("preview rewrite was accepted")

    assert store.events() == [{"kind": "preview_created", "preview": first}]
