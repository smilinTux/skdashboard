"""Public synthetic ASGI fixture for typed client and MCP development."""

from __future__ import annotations

import hashlib
import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .dashboard_reports import build_report_snapshot

ORIGIN = "https://synthetic.test"
BEARER = "fixture-read"
ACTION_CONTRACT_ID = "synthetic.action"
ACTION_CONTRACT_VERSION = "1.0.0"
ACTION_PREVIEW_HASH = "sha256:" + "b" * 64
WINDOW = {
    "start": "2026-08-24T00:00:00Z",
    "end": "2026-08-25T00:00:00Z",
    "timezone": "UTC",
    "baseline": None,
}
SCOPE = {"portfolio_id": "synthetic-estate"}
HASH = "sha256:" + "a" * 64


def _metric() -> dict:
    return {
        "schema_version": "1.1.0",
        "metric_id": "portfolio.synthetic_count",
        "definition_version": "1.0.0",
        "value": 1,
        "unit": "items",
        "polarity": "context_only",
        "numerator": 1,
        "denominator": None,
        "sample_size": 1,
        "scope": SCOPE,
        "window": WINDOW,
        "truth_state": "current",
        "visibility": {"state": "visible", "authorization": "authorized"},
        "measurement_kind": "measured",
        "source": {
            "owner": "synthetic.fixture",
            "adapter_id": "synthetic.fixture",
            "adapter_version": "1.0.0",
            "observed_at": "2026-08-24T12:00:00Z",
            "projected_at": "2026-08-24T12:00:30Z",
            "freshness_ttl_seconds": 300,
            "watermarks": [{"source": "synthetic.fixture", "value": "fixture-r1"}],
            "evidence_refs": ["evidence:synthetic:metric-r1"],
        },
        "data_quality": {
            "coverage_numerator": 1,
            "coverage_denominator": 1,
            "errors": [],
            "exclusions": ["production and protected content"],
        },
        "calculation": {"definition_hash": HASH, "method": "count"},
        "classification": {"level": "public", "purpose": "client qualification"},
    }


REPORT = build_report_snapshot(
    report_type="daily_operations",
    audience=["public synthetic development"],
    generated_at="2026-08-24T12:00:30Z",
    as_of="2026-08-24T12:00:00Z",
    scope=SCOPE,
    baseline=None,
    sections=[
        {
            "section_id": "synthetic",
            "title": "Public synthetic evidence",
            "metric_results": [_metric()],
            "insights": [],
        }
    ],
)

ACTION_PREVIEW = {
    "preview_id": "apv-synthetic-0001",
    "preview_hash": ACTION_PREVIEW_HASH,
    "schema_version": "1.0.0",
    "status": "ready",
    "source_recommendation_id": "rec-synthetic-0001",
    "action_contract_id": ACTION_CONTRACT_ID,
    "action_contract_version": ACTION_CONTRACT_VERSION,
    "owner_service": "synthetic.owner",
    "owner_operation": "apply_fixture",
    "target": {"resource_id": "synthetic-1"},
    "parameters": {"recommendation_id": "rec-synthetic-0001", "parameter_proposal_ref": "proposal:synthetic-0001"},
    "expected_version": "v1",
    "before_summary": "Synthetic state before the proposed action.",
    "proposed_effect": "Apply the public synthetic fixture action.",
    "blast_radius": "one synthetic resource",
    "risk": {"level": "low", "reasons": ["fixture only"]},
    "reversibility": "automatic",
    "verification_plan": ["Read the synthetic owner receipt."],
    "rollback_plan": ["Use the synthetic owner rollback operation."],
    "required_scope": "skdashboard.actions.authorize",
    "required_approvals": [{"approval_type": "synthetic", "state": "not_required"}],
    "policy_decision_ref": "policy:synthetic-action",
    "expires_at": "2099-01-01T00:00:00Z",
}

INSIGHT = {
    "insight_id": "ins-synthetic-abstention",
    "schema_version": "1.1.0",
    "status": "abstained",
    "kind": "brief",
    "summary": "The public synthetic fixture abstains from operational advice.",
    "scope": SCOPE,
    "window": WINDOW,
    "metric_refs": [],
    "evidence_refs": ["evidence:synthetic:metric-r1"],
    "calculation_refs": [],
    "uncertainty": ["No production owner evidence is available."],
    "contradictions": [],
    "exclusions": ["production state", "protected content"],
    "visibility": {"state": "visible", "authorization": "authorized"},
    "model_provenance": {
        "logical_route": "skdashboard.synthetic.fixture",
        "transport_profile": "offline-fixture",
        "gateway_revision": "not_applicable",
        "backend": "deterministic-fixture",
        "requested_model": "not_applicable",
        "served_model": "not_applicable",
        "model_revision": "not_applicable",
        "prompt_hash": HASH,
        "schema_hash": HASH,
    },
    "policy_decision_ref": "policy:synthetic-fixture",
    "abstention_reason": {
        "code": "insufficient_evidence",
        "message": "The fixture contains no production owner evidence.",
        "evidence_refs": ["evidence:synthetic:metric-r1"],
    },
    "recommendations": [],
    "next_steps": [
        {
            "label": "Open synthetic evidence",
            "kind": "open_evidence",
            "preview_only": True,
            "target_ref": "evidence:synthetic:metric-r1",
        }
    ],
}


def _envelope(items: list[dict], *, page: dict | None = None) -> dict:
    body = {
        "schema_version": "1.1.0",
        "request_id": "fixture-request",
        "source_owner": "synthetic.fixture",
        "scope": SCOPE,
        "freshness": {
            "truth_state": "current",
            "visibility": {"state": "visible", "authorization": "authorized"},
            "observed_at": "2026-08-24T12:00:00Z",
            "projected_at": "2026-08-24T12:00:30Z",
            "ttl_seconds": 300,
            "age_seconds": 30,
        },
        "visibility": {"state": "visible", "authorization": "authorized"},
        "metrics": [_metric()],
        "items": items,
        "errors": [],
    }
    if page is not None:
        body["page"] = page
    return body


def _etag_response(request, body: dict):
    content = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(content).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content, media_type="application/json", headers={"ETag": etag})


def _authorized(request, capability: str | None = None) -> bool:
    if request.headers.get("authorization") != f"Bearer {BEARER}":
        return False
    return capability is None or request.headers.get("x-sk-capability-name") == capability


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {
            "code": code,
            "message": message,
            "retryable": False,
            "request_id": "fixture-error",
        },
        status_code=status,
    )


def create_fixture_app() -> Starlette:
    async def manifest(_request):
        return JSONResponse(
            {
                "schemaVersion": "1.1",
                "id": "skdashboard-fixture",
                "name": "SK Control Plane public synthetic fixture",
                "grade": "B",
                "entry": {"url": f"{ORIGIN}/"},
                "auth": {"audience": "skdashboard", "scopes": ["skdashboard.read"]},
                "health": f"{ORIGIN}/api/v1/health",
                "actions": [{
                    "contract_id": ACTION_CONTRACT_ID,
                    "version": ACTION_CONTRACT_VERSION,
                    "operations": {
                        "preview": "/api/v1/actions/preview",
                        "authorize": "/api/v1/action-previews/{preview_id}/authorize",
                        "receipt": "/api/v1/action-receipts/{receipt_id}",
                        "verify": "/api/v1/action-receipts/{receipt_id}/verify",
                        "cancel": "/api/v1/action-receipts/{receipt_id}/cancel",
                        "rollback": "/api/v1/action-receipts/{receipt_id}/rollback",
                    },
                    "capabilities": {
                        "preview": "skdashboard.actions.preview",
                        "authorize": "skdashboard.actions.authorize",
                        "receipt": "skdashboard.actions.receipt",
                        "verify": "skdashboard.actions.verify",
                        "cancel": "skdashboard.actions.cancel",
                        "rollback": "skdashboard.actions.rollback",
                    },
                    "parameter_fields": ["recommendation_id", "parameter_proposal_ref"],
                }],
            }
        )

    async def health(request):
        return _etag_response(request, _envelope([{"component": "fixture", "state": "current"}]))

    async def projection(request):
        if not _authorized(request):
            return _error("FORBIDDEN", "The public synthetic read was denied.", 403)
        return _etag_response(
            request, _envelope([{"evidence_refs": ["evidence:synthetic:metric-r1"]}])
        )

    async def page(request):
        if not _authorized(request):
            return _error("FORBIDDEN", "The public synthetic read was denied.", 403)
        cursor = request.query_params.get("cursor")
        items = [{"item_id": "synthetic-2"}] if cursor else [{"item_id": "synthetic-1"}]
        state = {
            "limit": 1,
            "next_cursor": None if cursor else "fixture-page-2",
            "has_more": not bool(cursor),
        }
        return _etag_response(request, _envelope(items, page=state))

    async def report(request):
        if not _authorized(request) or request.path_params["snapshot_id"] != REPORT["snapshot_id"]:
            return _error("REPORT_NOT_FOUND", "The synthetic report was not found.", 404)
        return _etag_response(request, REPORT)

    async def insight(request):
        if not _authorized(request):
            return _error("FORBIDDEN", "The public synthetic query was denied.", 403)
        try:
            query = await request.json()
        except Exception:
            return _error("INVALID_QUERY", "The synthetic query was invalid.", 400)
        if not isinstance(query, dict):
            return _error("INVALID_QUERY", "The synthetic query was invalid.", 400)
        return _etag_response(request, INSIGHT)

    async def action_preview(request):
        if not _authorized(request, "skdashboard.actions.preview"):
            return _error("FORBIDDEN", "The synthetic action preview was denied.", 403)
        return _etag_response(request, ACTION_PREVIEW)

    async def action_authorize(request):
        if not _authorized(request, "skdashboard.actions.authorize"):
            return _error("FORBIDDEN", "The synthetic action authorization was denied.", 403)
        try:
            body = await request.json()
        except Exception:
            return _error("INVALID_ACTION", "The synthetic action request was invalid.", 400)
        if body.get("preview_hash") != ACTION_PREVIEW_HASH:
            return _error("PREVIEW_CHANGED", "The synthetic preview is stale.", 409)
        return _etag_response(request, {
            "receipt_id": "rcpt-synthetic-0001",
            "preview_id": ACTION_PREVIEW["preview_id"],
            "preview_hash": ACTION_PREVIEW_HASH,
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
        })

    async def action_receipt(request, *, authorize=True):
        if authorize and not _authorized(request, "skdashboard.actions.receipt"):
            return _error("FORBIDDEN", "The synthetic receipt was denied.", 403)
        return _etag_response(request, {
            "receipt_id": request.path_params["receipt_id"],
            "preview_id": ACTION_PREVIEW["preview_id"],
            "preview_hash": ACTION_PREVIEW_HASH,
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
        })

    async def action_verify(request):
        if not _authorized(request, "skdashboard.actions.verify"):
            return _error("FORBIDDEN", "The synthetic verification was denied.", 403)
        return await action_receipt(request, authorize=False)

    async def action_cancel(request):
        if not _authorized(request, "skdashboard.actions.cancel"):
            return _error("FORBIDDEN", "The synthetic cancellation was denied.", 403)
        response = await action_receipt(request, authorize=False)
        if response.status_code == 200:
            response.body = response.body.replace(b'"verification_state":"pending"', b'"verification_state":"rolled_back"')
        return response

    async def action_rollback(request):
        if not _authorized(request, "skdashboard.actions.rollback"):
            return _error("FORBIDDEN", "The synthetic rollback was denied.", 403)
        return await action_cancel(request)

    async def events(request):
        if not _authorized(request):
            return _error("FORBIDDEN", "The public synthetic stream was denied.", 403)
        cursor = request.query_params.get("cursor")
        prefix = (
            'event: reset-required\ndata: {"reason":"fixture replay unavailable"}\n\n'
            if cursor
            else ""
        )
        return Response(prefix + ": heartbeat\n\n", media_type="text/event-stream")

    return Starlette(
        routes=[
            Route("/.well-known/skworld-module.json", manifest),
            Route("/api/v1/health", health),
            Route("/api/v1/overview", projection),
            Route("/api/v1/board/summary", page),
            Route("/api/v1/fleet/summary", page),
            Route("/api/v1/economy/summary", page),
            Route("/api/v1/reports/{snapshot_id}", report),
            Route("/api/v1/insights/query", insight, methods=["POST"]),
            Route("/api/v1/actions/preview", action_preview, methods=["POST"]),
            Route("/api/v1/action-previews/{preview_id}/authorize", action_authorize, methods=["POST"]),
            Route("/api/v1/action-receipts/{receipt_id}", action_receipt),
            Route("/api/v1/action-receipts/{receipt_id}/verify", action_verify),
            Route("/api/v1/action-receipts/{receipt_id}/cancel", action_cancel, methods=["POST"]),
            Route("/api/v1/action-receipts/{receipt_id}/rollback", action_rollback, methods=["POST"]),
            Route("/api/v1/events", events),
        ]
    )


__all__ = ["BEARER", "INSIGHT", "ORIGIN", "REPORT", "create_fixture_app"]
