from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from jsonschema import ValidationError

from skdashboard.control_plane_client import (
    ClientResponse,
    ContractValidators,
    ControlPlaneClient,
    ControlPlaneClientError,
    _PreviewBinding,
    canonical_manifest_hash,
)
from skdashboard.control_plane_fixture import (
    BEARER,
    INSIGHT,
    MANIFEST,
    MANIFEST_SHA256,
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
        manifest_sha256=MANIFEST_SHA256,
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
    manifest["manifest_sha256"] = canonical_manifest_hash(manifest)

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


def test_discovery_requires_exact_caller_pinned_hash_and_rejects_origin_change() -> None:
    async def handler(_request):
        manifest = copy.deepcopy(MANIFEST)
        manifest["entry"] = {"url": "https://other.test/"}
        manifest["health"] = "https://other.test/api/v1/health"
        return httpx.Response(200, json=manifest)

    async def run() -> None:
        with pytest.raises(ControlPlaneClientError, match="caller-pinned"):
            await ControlPlaneClient.discover(
                DISCOVERY,
                BEARER,
                transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=MANIFEST)),
                manifest_sha256="malformed",
            )
        with pytest.raises(ControlPlaneClientError, match="caller-pinned"):
            await ControlPlaneClient.discover(
                DISCOVERY,
                BEARER,
                transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=MANIFEST)),
                manifest_sha256="0" * 64,
            )
        with pytest.raises(ControlPlaneClientError, match="canonical health route"):
            await ControlPlaneClient.discover(
                DISCOVERY,
                BEARER,
                transport=httpx.MockTransport(handler),
                manifest_sha256=MANIFEST_SHA256,
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


def test_action_preview_submit_and_receipt_poll_require_explicit_capabilities() -> None:
    async def run() -> None:
        client = await _client()
        try:
            with pytest.raises(ControlPlaneClientError, match="explicit action capability"):
                await client.preview_action(
                    "rec-1", "synthetic.noop", "proposal-1", capability=""
                )
            preview = await client.preview_action(
                "rec-1",
                "synthetic.noop",
                "proposal-1",
                capability="skdashboard.actions.preview",
            )
            assert preview.data["status"] == "ready"
            with pytest.raises(ControlPlaneClientError, match="explicit action capability"):
                await client.submit_action(
                    preview.data["preview_id"],
                    preview.data["preview_hash"],
                    "idempotency-key-0001",
                    "synthetic approval",
                    capability="",
                )
            submitted = await client.submit_action(
                preview.data["preview_id"],
                preview.data["preview_hash"],
                "idempotency-key-0001",
                "synthetic approval",
                capability="skdashboard.actions.authorize",
            )
            receipt = await client.poll_receipt(submitted.data["receipt_id"])
            assert receipt.data["verification_state"] == "passed"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_changed_preview_hash_is_rejected_before_authorize_transport() -> None:
    async def run() -> None:
        paths: list[str] = []
        delegate = httpx.ASGITransport(app=create_fixture_app())

        class CountingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                paths.append(request.url.path)
                return await delegate.handle_async_request(request)

        client = await ControlPlaneClient.discover(
            DISCOVERY,
            BEARER,
            transport=CountingTransport(),
            manifest_sha256=MANIFEST_SHA256,
        )
        try:
            preview = await client.preview_action(
                "rec-1", "synthetic.noop", "proposal-1", capability="skdashboard.actions.preview"
            )
            before = len(paths)
            with pytest.raises(ControlPlaneClientError, match="unknown or changed"):
                await client.submit_action(
                    preview.data["preview_id"],
                    "sha256:" + "b" * 64,
                    "idempotency-key-changed-hash",
                    "synthetic approval",
                    capability="skdashboard.actions.authorize",
                )
            assert len(paths) == before
        finally:
            await client.aclose()

    asyncio.run(run())


def test_unrelated_valid_receipt_id_is_rejected_before_receipt_transport() -> None:
    async def run() -> None:
        paths: list[str] = []
        delegate = httpx.ASGITransport(app=create_fixture_app())

        class CountingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                paths.append(request.url.path)
                return await delegate.handle_async_request(request)

        client = await ControlPlaneClient.discover(
            DISCOVERY,
            BEARER,
            transport=CountingTransport(),
            manifest_sha256=MANIFEST_SHA256,
        )
        try:
            before = len(paths)
            with pytest.raises(ControlPlaneClientError, match="unknown"):
                await client.poll_receipt("cmdr-unrelated-request")
            assert len(paths) == before
        finally:
            await client.aclose()

    asyncio.run(run())


def test_preview_is_one_use_across_changed_key_and_concurrent_submissions() -> None:
    async def run() -> None:
        calls: list[str] = []
        delegate = httpx.ASGITransport(app=create_fixture_app())

        class CountingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if request.method == "POST" and request.url.path.endswith("/authorize"):
                    calls.append(request.url.path)
                return await delegate.handle_async_request(request)

        client = await ControlPlaneClient.discover(
            DISCOVERY,
            BEARER,
            transport=CountingTransport(),
            manifest_sha256=MANIFEST_SHA256,
        )
        try:
            preview = await client.preview_action(
                "rec-replay", "synthetic.noop", "proposal-replay", capability="skdashboard.actions.preview"
            )
            kwargs = {
                "preview_id": preview.data["preview_id"],
                "preview_hash": preview.data["preview_hash"],
                "approval_reason": "synthetic replay regression",
                "capability": "skdashboard.actions.authorize",
            }
            outcomes = await asyncio.gather(
                client.submit_action(idempotency_key="idempotency-replay-0001", **kwargs),
                client.submit_action(idempotency_key="idempotency-replay-0002", **kwargs),
                return_exceptions=True,
            )
            assert sum(isinstance(outcome, ClientResponse) for outcome in outcomes) == 1
            assert sum(isinstance(outcome, ControlPlaneClientError) for outcome in outcomes) == 1
            first = next(outcome for outcome in outcomes if isinstance(outcome, ClientResponse))
            with pytest.raises(ControlPlaneClientError, match="already been used"):
                await client.submit_action(idempotency_key="idempotency-replay-0001", **kwargs)
            with pytest.raises(ControlPlaneClientError, match="already been used"):
                await client.submit_action(idempotency_key="idempotency-replay-0003", **kwargs)
            assert first.data["receipt_id"] == "cmdr-synthetic-1"
            assert len(calls) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_preview_is_consumed_before_first_dispatch_failure() -> None:
    async def run() -> None:
        calls = 0
        delegate = httpx.ASGITransport(app=create_fixture_app())

        class FailingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                nonlocal calls
                if request.method == "POST" and request.url.path.endswith("/authorize"):
                    calls += 1
                    raise httpx.ConnectError("synthetic first-dispatch failure", request=request)
                return await delegate.handle_async_request(request)

        client = await ControlPlaneClient.discover(
            DISCOVERY,
            BEARER,
            transport=FailingTransport(),
            manifest_sha256=MANIFEST_SHA256,
        )
        try:
            preview = await client.preview_action(
                "rec-failure", "synthetic.noop", "proposal-failure", capability="skdashboard.actions.preview"
            )
            kwargs = {
                "preview_id": preview.data["preview_id"],
                "preview_hash": preview.data["preview_hash"],
                "approval_reason": "synthetic failure regression",
                "capability": "skdashboard.actions.authorize",
            }
            with pytest.raises(httpx.ConnectError):
                await client.submit_action(idempotency_key="idempotency-failure-0001", **kwargs)
            with pytest.raises(ControlPlaneClientError, match="already been used"):
                await client.submit_action(idempotency_key="idempotency-failure-0002", **kwargs)
            assert calls == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_preview_binding_rejects_changed_input_expiry_and_arbitrary_parameters() -> None:
    async def run() -> None:
        client = await _client()
        try:
            with pytest.raises(ControlPlaneClientError, match="protected"):
                await client.preview_action(
                    "rec-1",
                    "synthetic.noop",
                    "matter-ref",
                    capability="skdashboard.actions.preview",
                )
            preview = await client.preview_action(
                "rec-1", "synthetic.noop", "proposal-1", capability="skdashboard.actions.preview"
            )
            binding = client._previews[preview.data["preview_id"]]
            client._previews[preview.data["preview_id"]] = _PreviewBinding(
                preview_id=binding.preview_id,
                preview_hash=binding.preview_hash,
                status="stale",
                expires_at=binding.expires_at,
                target=binding.target,
                parameters=binding.parameters,
            )
            with pytest.raises(ControlPlaneClientError, match="stale"):
                await client.submit_action(
                    preview.data["preview_id"],
                    preview.data["preview_hash"],
                    "idempotency-key-stale",
                    "synthetic approval",
                    capability="skdashboard.actions.authorize",
                )
            client._previews[preview.data["preview_id"]] = _PreviewBinding(
                preview_id=binding.preview_id,
                preview_hash=binding.preview_hash,
                status=binding.status,
                expires_at="2000-01-01T00:00:00Z",
                target=binding.target,
                parameters=binding.parameters,
            )
            with pytest.raises(ControlPlaneClientError, match="stale or expired"):
                await client.submit_action(
                    preview.data["preview_id"],
                    preview.data["preview_hash"],
                    "idempotency-key-expired",
                    "synthetic approval",
                    capability="skdashboard.actions.authorize",
                )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_receipt_response_must_match_submitted_preview() -> None:
    async def run() -> None:
        client = await _client()
        try:
            preview = await client.preview_action(
                "rec-1", "synthetic.noop", "proposal-1", capability="skdashboard.actions.preview"
            )
            original = client._http

            async def handler(request):
                if request.url.path.endswith("action-previews/apv-synthetic-action-1/authorize"):
                    return httpx.Response(
                        202,
                        json={
                            "receipt_id": "cmdr-synthetic-1",
                            "preview_id": preview.data["preview_id"],
                            "preview_hash": "sha256:" + "b" * 64,
                            "status": "accepted",
                            "owner_service": "synthetic.fixture",
                            "owner_receipt_ref": "owner:synthetic-1",
                            "policy_decision_ref": "policy:synthetic-fixture",
                            "accepted_at": "2026-08-24T12:00:30Z",
                            "verification_state": "passed",
                        },
                    )
                return await original.send(request)

            client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            with pytest.raises(ControlPlaneClientError, match="not bound"):
                await client.submit_action(
                    preview.data["preview_id"],
                    preview.data["preview_hash"],
                    "idempotency-key-receipt",
                    "synthetic approval",
                    capability="skdashboard.actions.authorize",
                )
            await client._http.aclose()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_receipt_poll_timeout_and_cancellation_are_fail_closed() -> None:
    async def run() -> None:
        client = await _client()
        try:
            preview = await client.preview_action(
                "rec-1", "synthetic.noop", "proposal-1", capability="skdashboard.actions.preview"
            )
            client._receipts["cmdr-pending-1"] = (
                preview.data["preview_id"],
                preview.data["preview_hash"],
            )
            with pytest.raises(TimeoutError):
                    await client.poll_receipt("cmdr-pending-1", timeout=0.01, interval=0.005)
            cancelled = asyncio.Event()
            cancelled.set()
            with pytest.raises(asyncio.CancelledError):
                    await client.poll_receipt("cmdr-pending-1", cancel_event=cancelled)
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
            for receipt_id in ("../../owner-state", "cmdr-%2e%2e%2fowner-state"):
                with pytest.raises(ControlPlaneClientError, match="receipt id"):
                    await client.poll_receipt(receipt_id)
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


def test_client_validates_frozen_error_contract_before_exposing_status() -> None:
    async def valid_error(request):
        if request.url.path.endswith("skworld-module.json"):
                manifest = copy.deepcopy(MANIFEST)
                return httpx.Response(
                    200,
                    json=manifest,
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
            DISCOVERY,
            BEARER,
            transport=httpx.MockTransport(valid_error),
            manifest_sha256=MANIFEST_SHA256,
        )
        try:
            with pytest.raises(ControlPlaneClientError, match="503 SOURCE_UNAVAILABLE"):
                await client.health()
        finally:
            await client.aclose()
        client = await ControlPlaneClient.discover(
            DISCOVERY,
            BEARER,
            transport=httpx.MockTransport(invalid_error),
            manifest_sha256=MANIFEST_SHA256,
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
