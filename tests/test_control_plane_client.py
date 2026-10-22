from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from jsonschema import ValidationError

from skdashboard.control_plane_client import (
    ActionClientError,
    ContractValidators,
    ControlPlaneClient,
    ControlPlaneClientError,
)
from skdashboard.control_plane_fixture import (
    ACTION_PREVIEW,
    ACTION_PREVIEW_HASH,
    BEARER,
    INSIGHT,
    ORIGIN,
    REPORT,
    SCOPE,
    WINDOW,
    create_fixture_app,
)

DISCOVERY = ORIGIN + "/.well-known/skworld-module.json"


async def _client() -> ControlPlaneClient:
    return await ControlPlaneClient.discover(
        DISCOVERY,
        BEARER,
        transport=httpx.ASGITransport(app=create_fixture_app()),
    )


def _insight_query() -> dict:
    return {
        "question": "Summarize the public synthetic portfolio evidence.",
        "scope": SCOPE,
        "window": WINDOW,
        "intent": "brief",
        "metric_families": ["portfolio"],
        "baseline": None,
    }


def test_client_discovers_same_origin_and_never_discloses_bearer() -> None:
    async def run() -> None:
        client = await _client()
        try:
            assert client.origin == ORIGIN
            assert client.manifest["health"] == ORIGIN + "/api/v1/health"
            assert BEARER not in repr(client)
            assert not any(BEARER in json.dumps(value) for value in client.manifest.values())
        finally:
            await client.aclose()

    asyncio.run(run())


def test_discovery_rejects_redirects_cross_origin_and_non_https() -> None:
    manifest = {
        "schemaVersion": "1.1",
        "entry": {"url": "https://other.test/"},
        "auth": {"audience": "skdashboard", "scopes": ["skdashboard.read"]},
        "health": "https://other.test/api/v1/health",
    }

    async def handler(_request):
        return httpx.Response(200, json=manifest)

    async def run() -> None:
        with pytest.raises(ControlPlaneClientError, match="canonical health route"):
            await ControlPlaneClient.discover(
                DISCOVERY, BEARER, transport=httpx.MockTransport(handler)
            )
        with pytest.raises(ControlPlaneClientError, match="canonical HTTPS"):
            await ControlPlaneClient.discover(
                "http://synthetic.test/.well-known/skworld-module.json", BEARER
            )

    asyncio.run(run())


def test_client_reads_validates_etag_and_keeps_cached_data_immutable() -> None:
    async def run() -> None:
        client = await _client()
        try:
            first = await client.health()
            first.data["items"][0]["state"] = "tampered"
            unchanged = await client.health()
            assert unchanged.not_modified is True
            assert unchanged.etag == first.etag
            assert unchanged.data["items"][0]["state"] == "current"

            overview = await client.overview()
            metric = overview.data["metrics"][0]
            assert metric["metric_id"] == "portfolio.synthetic_count"
            assert metric["truth_state"] == "current"
            assert metric["calculation"]["definition_hash"]
            assert metric["data_quality"]["errors"] == []
            assert metric["source"]["evidence_refs"] == ["evidence:synthetic:metric-r1"]
            assert client.evidence_refs(overview.data) == ["evidence:synthetic:metric-r1"]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_ui_api_document_and_typed_client_agree_on_measurement_semantics() -> None:
    async def run() -> None:
        transport = httpx.ASGITransport(app=create_fixture_app())
        async with httpx.AsyncClient(transport=transport) as raw_http:
            raw = await raw_http.get(
                ORIGIN + "/api/v1/overview",
                headers={"Authorization": f"Bearer {BEARER}"},
            )
        client = await _client()
        try:
            typed = await client.overview()
            assert typed.data == raw.json()
            metric = typed.data["metrics"][0]
            assert {
                "value": metric["value"],
                "definition_hash": metric["calculation"]["definition_hash"],
                "scope": metric["scope"],
                "truth_state": metric["truth_state"],
                "freshness": typed.data["freshness"],
                "quality": metric["data_quality"],
                "evidence_refs": metric["source"]["evidence_refs"],
            } == {
                "value": 1,
                "definition_hash": "sha256:" + "a" * 64,
                "scope": SCOPE,
                "truth_state": "current",
                "freshness": raw.json()["freshness"],
                "quality": raw.json()["metrics"][0]["data_quality"],
                "evidence_refs": ["evidence:synthetic:metric-r1"],
            }
        finally:
            await client.aclose()

    asyncio.run(run())


def test_report_insight_saved_scope_and_metric_family_follow_frozen_schemas() -> None:
    async def run() -> None:
        client = await _client()
        try:
            report = await client.report(REPORT["snapshot_id"])
            insight = await client.insight(_insight_query())
            scope = await client.saved_scope({"project_id": "synthetic-estate"})
            metrics = await client.metric_family("portfolio")

            assert report.data == REPORT
            assert insight.data == INSIGHT
            assert insight.data["status"] == "abstained"
            assert scope.data["scope"] == SCOPE
            assert metrics == [scope.data["metrics"][0]]
            assert report.data["sections"][0]["metric_results"][0] == metrics[0]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_pagination_event_resume_and_reset_are_bounded() -> None:
    async def run() -> None:
        client = await _client()
        try:
            pages = [page async for page in client.pages("board", limit="1")]
            assert [page.data["items"][0]["item_id"] for page in pages] == [
                "synthetic-1",
                "synthetic-2",
            ]
            events = await client.events(cursor="djE6MQ", topics=("reports",))
            assert events == [
                {"event": "reset-required", "data": {"reason": "fixture replay unavailable"}}
            ]
            with pytest.raises(ControlPlaneClientError, match="topics"):
                await client.events(topics=tuple(str(index) for index in range(17)))
            with pytest.raises(ControlPlaneClientError, match="pagination"):
                async for _page in client.pages("overview"):
                    pass
        finally:
            await client.aclose()

    asyncio.run(run())


def test_client_rejects_arbitrary_queries_ids_operations_and_invalid_contracts() -> None:
    async def run() -> None:
        client = await _client()
        try:
            with pytest.raises(ControlPlaneClientError, match="allowlist"):
                await client.overview({"matter_id": "protected"})
            with pytest.raises(ControlPlaneClientError, match="snapshot id"):
                await client.report("../../owner-state")
            with pytest.raises(ControlPlaneClientError, match="schema validation"):
                await client.insight({"question": "missing required fields"})
            with pytest.raises(ControlPlaneClientError, match="metric family"):
                await client.metric_family("individual-ranking")
        finally:
            await client.aclose()

    asyncio.run(run())

    bad_report = copy.deepcopy(REPORT)
    bad_report["sections"][0]["metric_results"][0]["truth_state"] = "healthy"
    with pytest.raises(ControlPlaneClientError, match="schema validation"):
        ContractValidators().validate("report", bad_report)


def test_allowlisted_action_lifecycle_uses_discovered_templates_and_names_only() -> None:
    async def run() -> None:
        client = await _client()
        try:
            assert len(client.action_contracts()) == 1
            preview = await client.preview_action(
                "synthetic.action",
                {"recommendation_id": "rec-synthetic-0001", "parameter_proposal_ref": "proposal:synthetic-0001"},
            )
            assert preview.data["preview_hash"].startswith("sha256:")
            receipt = await client.authorize_action(
                "synthetic.action", "apv-synthetic-0001", preview.data["preview_hash"],
                "idempotency-action-0001", "synthetic qualification",
            )
            assert receipt.data["verification_state"] == "pending"
            verified = await client.verify_action("synthetic.action", "rcpt-synthetic-0001")
            assert verified.data["owner_receipt_ref"] == "owner-receipt:synthetic-0001"
            canceled = await client.cancel_action("synthetic.action", "rcpt-synthetic-0001")
            assert canceled.data["verification_state"] == "rolled_back"
            rolled_back = await client.rollback_action("synthetic.action", "rcpt-synthetic-0001")
            assert rolled_back.data["verification_state"] == "rolled_back"
            assert "fixture-read" not in json.dumps(receipt.data)
            assert "skdashboard.actions.authorize" not in json.dumps(receipt.data)
        finally:
            await client.aclose()

    asyncio.run(run())


def test_fixture_action_idempotency_and_terminal_verification_are_stateful() -> None:
    async def run() -> None:
        client = await _client()
        try:
            preview = await client.preview_action(
                "synthetic.action",
                {"recommendation_id": "rec-synthetic-0001", "parameter_proposal_ref": "proposal:synthetic-0001"},
            )
            first = await client.authorize_action(
                "synthetic.action", preview.data["preview_id"], preview.data["preview_hash"],
                "idempotency-action-0001", "synthetic qualification",
            )
            duplicate = await client.authorize_action(
                "synthetic.action", preview.data["preview_id"], preview.data["preview_hash"],
                "idempotency-action-0001", "synthetic qualification",
            )
            changed = await client.authorize_action(
                "synthetic.action", preview.data["preview_id"], preview.data["preview_hash"],
                "idempotency-action-0002", "synthetic qualification",
            )
            verified = await client.verify_action("synthetic.action", first.data["receipt_id"])
            assert first.data["status"] == "accepted"
            assert duplicate.data["status"] == "duplicate"
            assert duplicate.data["receipt_id"] == first.data["receipt_id"]
            assert changed.data["receipt_id"] != first.data["receipt_id"]
            assert verified.data["verification_state"] == "verified"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_action_receipt_rejects_unexpected_and_credential_like_fields() -> None:
    async def run() -> None:
        unsafe = {
            "receipt_id": "rcpt-synthetic-0001",
            "preview_id": "apv-synthetic-0001",
            "preview_hash": "sha256:" + "b" * 64,
            "status": "accepted",
            "owner_service": "synthetic.owner",
            "owner_receipt_ref": "owner-receipt:synthetic-0001",
            "policy_decision_ref": "policy:synthetic-action",
            "accepted_at": "2026-08-24T12:00:30Z",
            "verification_state": "pending",
            "actor_ref": "sha256:" + "c" * 64,
            "purpose": "synthetic qualification",
            "audit_ref": "audit:synthetic-0001",
            "external_effect_state": "none",
            "authorization": " ".join(("Bearer", "REDACTED_SYNTHETIC_VALUE")),
        }

        async def handler(_request):
            return httpx.Response(200, json=unsafe)

        manifest = {
            "schemaVersion": "1.1",
            "entry": {"url": ORIGIN + "/"},
            "auth": {"audience": "skdashboard"},
            "health": ORIGIN + "/api/v1/health",
            "actions": [{
                "contract_id": "synthetic.action",
                "version": "1.0.0",
                "operations": {"receipt": "/api/v1/action-receipts/{receipt_id}"},
                "capabilities": {"receipt": "skdashboard.actions.receipt"},
            }],
        }
        client = ControlPlaneClient(
            ORIGIN,
            BEARER,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            manifest=manifest,
            owns_http=False,
        )
        try:
            with pytest.raises(ActionClientError, match="protected or credential-like"):
                await client.receipt("synthetic.action", "rcpt-synthetic-0001")
        finally:
            await client.aclose()

    asyncio.run(run())


def test_action_response_adversarial_receipts_fail_closed_before_output() -> None:
    base = {
        "receipt_id": "rcpt-synthetic-0001",
        "preview_id": "apv-synthetic-0001",
        "preview_hash": "sha256:" + "b" * 64,
        "status": "accepted",
        "owner_service": "synthetic.owner",
        "owner_receipt_ref": "owner-receipt:synthetic-0001",
        "policy_decision_ref": "policy:synthetic-action",
        "accepted_at": "2026-08-24T12:00:30Z",
        "verification_state": "pending",
        "actor_ref": "sha256:" + "c" * 64,
        "purpose": "synthetic qualification",
        "audit_ref": "audit:synthetic-0001",
        "external_effect_state": "none",
        "action_contract_id": "synthetic.action",
        "action_contract_version": "1.0.0",
        "target": {"resource_id": "synthetic-1"},
        "parameters": {"recommendation_id": "rec-synthetic-0001", "parameter_proposal_ref": "proposal:synthetic-0001"},
        "idempotency_key": "idempotency-action-0001",
    }
    mutations = {
        "unexpected": {"unexpected": True},
        "bearer": {"authorization": " ".join(("Bearer", "REDACTED_SYNTHETIC_VALUE"))},
        "matter_alias": {"matter_id": "REDACTED_SYNTHETIC_VALUE"},
        "matter_payload": {"matter_payload": "protected Matter content"},
        "endpoint": {"endpoint": "https://attacker.invalid"},
        "receipt_identity": {"receipt_id": "rcpt-other-0001"},
        "preview_hash": {"preview_hash": "sha256:" + "a" * 64},
        "target": {"target": {"resource_id": "other"}},
        "parameters": {"parameters": {"recommendation_id": "other", "parameter_proposal_ref": "proposal:synthetic-0001"}},
        "idempotency": {"idempotency_key": "idempotency-other-0001"},
    }

    expected_errors = {
        "unexpected": "action response failed its strict typed schema",
        "bearer": "action response contains protected or credential-like output",
        "matter_alias": "action response contains protected or credential-like output",
        "matter_payload": "action response contains protected or credential-like output",
        "endpoint": "action response contains protected or credential-like output",
        "receipt_identity": "action receipt identity does not match the request",
        "preview_hash": "action receipt binding changed",
        "target": "action receipt binding changed",
        "parameters": "action receipt binding changed",
        "idempotency": "action receipt binding changed",
    }

    async def run() -> None:
        for name, change in mutations.items():
            async def handler(_request, change=change):
                return httpx.Response(200, json={**base, **change})

            manifest = {
                "schemaVersion": "1.1", "entry": {"url": ORIGIN + "/"},
                "auth": {"audience": "skdashboard"}, "health": ORIGIN + "/api/v1/health",
                "actions": [{
                    "contract_id": "synthetic.action", "version": "1.0.0",
                    "operations": {"receipt": "/api/v1/action-receipts/{receipt_id}"},
                    "capabilities": {"receipt": "skdashboard.actions.receipt"},
                }],
            }
            client = ControlPlaneClient(
                ORIGIN, BEARER, httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                manifest=manifest, owns_http=False,
            )
            client._receipts["rcpt-synthetic-0001"] = copy.deepcopy(base)
            try:
                with pytest.raises(ActionClientError, match=expected_errors[name]):
                    await client.receipt("synthetic.action", "rcpt-synthetic-0001")
            finally:
                await client.aclose()

    asyncio.run(run())


def test_action_template_and_changed_input_fail_closed_before_dispatch() -> None:
    async def run() -> None:
        client = await _client()
        try:
            preview = await client.preview_action(
                "synthetic.action",
                {"recommendation_id": "rec-synthetic-0001", "parameter_proposal_ref": "proposal:synthetic-0001"},
            )
            with pytest.raises(ActionClientError, match="changed target"):
                await client.authorize_action(
                    "synthetic.action", "apv-synthetic-0001", preview.data["preview_hash"],
                    "idempotency-action-0002", "synthetic qualification", target={"resource_id": "other"},
                )
        finally:
            await client.aclose()

    asyncio.run(run())

    bad_manifest = {
        "schemaVersion": "1.1",
        "entry": {"url": ORIGIN + "/"},
        "auth": {"audience": "skdashboard"},
        "health": ORIGIN + "/api/v1/health",
        "actions": [{
            "contract_id": "bad",
            "version": "1.0.0",
            "operations": {"receipt": "/api/v1/action-receipts/{preview_id}"},
            "capabilities": {"receipt": "skdashboard.actions.receipt"},
        }],
    }
    with pytest.raises(ControlPlaneClientError, match="action contract"):
        ControlPlaneClient._load_action_contracts(bad_manifest)


def test_action_boundaries_reject_arbitrary_routes_fields_expiry_and_unsafe_receipts() -> None:
    async def run() -> None:
        calls = []

        async def handler(request):
            calls.append((request.method, request.url.path))
            return httpx.Response(200, json={**copy.deepcopy(ACTION_PREVIEW), "expires_at": "2099-01-01T00:00:00Z"})

        manifest = {
            "schemaVersion": "1.1",
            "entry": {"url": ORIGIN + "/"},
            "auth": {"audience": "skdashboard"},
            "health": ORIGIN + "/api/v1/health",
            "actions": [{
                "contract_id": "synthetic.action",
                "version": "1.0.0",
                "operations": {"preview": "/api/v1/admin/delete-all"},
                "capabilities": {"preview": "skdashboard.actions.preview"},
                "parameter_fields": ["recommendation_id", "parameter_proposal_ref"],
            }],
        }
        with pytest.raises(ControlPlaneClientError, match="action contract"):
            ControlPlaneClient._load_action_contracts(manifest)
        manifest["actions"][0]["operations"]["preview"] = "/api/v1/actions/preview"
        manifest["actions"][0]["parameter_fields"] = ["matter_id"]
        with pytest.raises(ControlPlaneClientError, match="action contract"):
            ControlPlaneClient._load_action_contracts(manifest)

        client = await _client()
        try:
            client._previews["apv-synthetic-0001"] = {
                **copy.deepcopy(ACTION_PREVIEW),
                "expires_at": "2000-01-01T00:00:00Z",
            }
            with pytest.raises(ActionClientError, match="expired"):
                await client.authorize_action(
                    "synthetic.action", "apv-synthetic-0001", ACTION_PREVIEW_HASH,
                    "idempotency-expired-0001", "synthetic qualification",
                )
            assert calls == []
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "capabilities",
    [
        {},
        {"preview": ""},
        {"preview": "skdashboard.actions.other"},
        {"preview": "skdashboard.actions.preview", "authorize": "skdashboard.actions.authorize", "receipt": "skdashboard.actions.receipt", "verify": "skdashboard.actions.verify", "cancel": "skdashboard.actions.cancel", "rollback": "skdashboard.actions.rollback", "extra": "skdashboard.actions.extra"},
    ],
)
def test_discovery_rejects_missing_empty_mismatched_or_undeclared_capability_names(capabilities) -> None:
    manifest = {
        "actions": [{
            "contract_id": "synthetic.action",
            "version": "1.0.0",
            "operations": {"preview": "/api/v1/actions/preview"},
            "capabilities": capabilities,
        }],
    }
    with pytest.raises(ControlPlaneClientError, match="action contract"):
        ControlPlaneClient._load_action_contracts(manifest)


def test_rollback_uses_rollback_only_capability_without_cancel() -> None:
    async def run() -> None:
        receipt = {
            "receipt_id": "rcpt-synthetic-0001",
            "preview_id": "apv-synthetic-0001",
            "preview_hash": ACTION_PREVIEW_HASH,
            "status": "rolled_back",
            "owner_service": "synthetic.owner",
            "owner_receipt_ref": "owner-receipt:synthetic-0001",
            "policy_decision_ref": "policy:synthetic-action",
            "accepted_at": "2026-08-24T12:00:30Z",
            "verification_state": "rolled_back",
            "actor_ref": "sha256:" + "c" * 64,
            "purpose": "synthetic qualification",
            "audit_ref": "audit:synthetic-0001",
            "external_effect_state": "none",
            "action_contract_id": "synthetic.action",
            "action_contract_version": "1.0.0",
            "target": {"resource_id": "synthetic-1"},
            "parameters": {"recommendation_id": "rec-synthetic-0001", "parameter_proposal_ref": "proposal:synthetic-0001"},
            "idempotency_key": "idempotency-action-0001",
        }
        calls = []

        async def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json=receipt)

        manifest = {
            "schemaVersion": "1.1", "entry": {"url": ORIGIN + "/"},
            "auth": {"audience": "skdashboard"}, "health": ORIGIN + "/api/v1/health",
            "actions": [{
                "contract_id": "synthetic.action", "version": "1.0.0",
                "operations": {"rollback": "/api/v1/action-receipts/{receipt_id}/rollback"},
                "capabilities": {"rollback": "skdashboard.actions.rollback"},
            }],
        }
        client = ControlPlaneClient(
            ORIGIN, BEARER, httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            manifest=manifest, owns_http=False,
        )
        client._receipts[receipt["receipt_id"]] = copy.deepcopy(receipt)
        try:
            rolled_back = await client.rollback_action("synthetic.action", receipt["receipt_id"])
            assert rolled_back.data["verification_state"] == "rolled_back"
            with pytest.raises(ActionClientError, match="not declared"):
                await client.cancel_action("synthetic.action", receipt["receipt_id"])
            assert calls == ["/api/v1/action-receipts/rcpt-synthetic-0001/rollback"]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_client_validates_frozen_error_contract_before_exposing_status() -> None:
    async def valid_error(request):
        if request.url.path.endswith("skworld-module.json"):
            return httpx.Response(
                200,
                json={
                    "schemaVersion": "1.1",
                    "entry": {"url": ORIGIN + "/"},
                    "auth": {"audience": "skdashboard", "scopes": ["skdashboard.read"]},
                    "health": ORIGIN + "/api/v1/health",
                },
            )
        return httpx.Response(
            503,
            json={
                "code": "SOURCE_UNAVAILABLE",
                "message": "The public synthetic source is unavailable.",
                "retryable": True,
                "request_id": "fixture-error",
            },
        )

    async def invalid_error(request):
        response = await valid_error(request)
        if response.status_code != 200:
            return httpx.Response(503, json={"code": "SOURCE_UNAVAILABLE"})
        return response

    async def run() -> None:
        client = await ControlPlaneClient.discover(
            DISCOVERY, BEARER, transport=httpx.MockTransport(valid_error)
        )
        try:
            with pytest.raises(ControlPlaneClientError, match="503 SOURCE_UNAVAILABLE"):
                await client.health()
        finally:
            await client.aclose()
        client = await ControlPlaneClient.discover(
            DISCOVERY, BEARER, transport=httpx.MockTransport(invalid_error)
        )
        try:
            with pytest.raises(ControlPlaneClientError, match="schema validation"):
                await client.health()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_published_contract_copies_are_exact_and_schema_validators_are_sensitive() -> None:
    validators = ContractValidators()
    validators.validate("report", REPORT)
    validators.validate("insight", INSIGHT)
    validators.validate("insight_query", _insight_query())
    with pytest.raises(ValidationError):
        validators.validators["insight_query"].validate(
            {**_insight_query(), "metric_families": ["people-ranking"]}
        )

    from pathlib import Path

    root = Path(__file__).parents[1]
    names = {
        "openapi.control-plane.v1.1.0.json",
        "control-plane-metric-result.v1.1.0.schema.json",
        "control-plane-recommendation.v1.1.0.schema.json",
        "control-plane-insight.v1.1.0.schema.json",
        "control-plane-report-snapshot.v1.1.0.schema.json",
    }
    for name in names:
        assert (root / "src/skdashboard/contracts/v1.1.0" / name).read_bytes() == (
            root / "docs/contracts/v1.1.0" / name
        ).read_bytes()
