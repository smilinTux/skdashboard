"""Run the local SKCP-40 receipt boundary probe without network access."""

from __future__ import annotations

import asyncio
import copy

import httpx

from skdashboard.control_plane_client import ActionClientError, ControlPlaneClient
from skdashboard.control_plane_fixture import BEARER, ORIGIN

BASE_RECEIPT = {
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
    "parameters": {
        "recommendation_id": "rec-synthetic-0001",
        "parameter_proposal_ref": "proposal:synthetic-0001",
    },
    "idempotency_key": "idempotency-action-0001",
}


def _manifest() -> dict:
    return {
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


async def main() -> None:
    mutations = {
        "unexpected": {"unexpected": True},
        "bearer": {"authorization": " ".join(("Bearer", "REDACTED_SYNTHETIC_VALUE"))},
        "matter_id": {"matter_id": "REDACTED_SYNTHETIC_VALUE"},
        "matter_payload": {"matter_payload": "protected Matter content"},
        "endpoint": {"endpoint": "https://attacker.invalid"},
        "receipt_identity": {"receipt_id": "rcpt-other-0001"},
        "preview_hash": {"preview_hash": "sha256:" + "a" * 64},
        "target": {"target": {"resource_id": "other"}},
        "parameters": {"parameters": {"recommendation_id": "other"}},
        "idempotency": {"idempotency_key": "idempotency-other-0001"},
    }
    for name, mutation in mutations.items():
        async def handler(_request, mutation=mutation):
            return httpx.Response(200, json={**BASE_RECEIPT, **mutation})

        client = ControlPlaneClient(
            ORIGIN,
            BEARER,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            manifest=_manifest(),
            owns_http=False,
        )
        client._receipts[BASE_RECEIPT["receipt_id"]] = copy.deepcopy(BASE_RECEIPT)
        try:
            try:
                await client.receipt("synthetic.action", BASE_RECEIPT["receipt_id"])
            except ActionClientError:
                print(f"{name}=PASS")
            else:
                raise AssertionError(f"{name} receipt was accepted")
        finally:
            await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
