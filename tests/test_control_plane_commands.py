from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from capauth import (
    ClientKind,
    ControlPlaneAuthorizationResultV1,
    DecisionCode,
    DecisionState,
    RequestBoundary,
)
from starlette.testclient import TestClient

from skdashboard import control_plane_api
from skdashboard.control_plane_commands import (
    COMMAND_SPECS,
    CommandGateway,
    IdempotencyClaim,
    InMemoryIdempotencyStore,
    JsonlCommandAudit,
    OwnerCommandResult,
)
from skdashboard.dashboard import create_app

UTC = timezone.utc
ORIGIN = "https://10.0.0.139:7778"
REVISION = "a" * 64
ACTOR_REF = "sha256:" + "b" * 64


class Audit:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)
        return f"audit:{len(self.events)}"


class Owner:
    def __init__(self, results=None, delay=0):
        self.calls = []
        self.authorizations = []
        self.results = list(results or [])
        self.delay = delay

    async def invoke(self, command, authorization):
        self.calls.append(command)
        self.authorizations.append(authorization)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.results:
            value = self.results.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return OwnerCommandResult(
            status="previewed" if command.preview else "applied",
            result_code="PREVIEW_READY" if command.preview else "APPLIED",
            owner_version=command.expected_version if command.preview else "v2",
            owner_audit_ref="owner-audit:1",
            external_effect_state="none",
        )


class Verifier:
    def close(self):
        pass


class Authorizer:
    def __init__(self, state=DecisionState.ALLOW):
        self.state = state

    def authorize_with_currentness(self, _bearer, invocation):
        if self.state is DecisionState.UNAVAILABLE:
            return (
                ControlPlaneAuthorizationResultV1(
                    allow=False,
                    state=DecisionState.UNAVAILABLE,
                    code=DecisionCode.CAPAUTH_UNAVAILABLE,
                ),
                Verifier(),
            )
        binding = SimpleNamespace(
            principal=SimpleNamespace(subject="human@example.test"),
            node_id=invocation.node_id,
            purpose=invocation.purpose,
            audience=invocation.audience,
            capability=invocation.capability,
            target=invocation.target,
            resource_type=invocation.resource_type,
            resource_id=invocation.resource_id,
            owner_policy_revision=REVISION,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
            capability_scope=lambda: "scope",
        )
        context = SimpleNamespace(
            boundary=invocation.boundary,
            binding=binding,
            capauth_decision=SimpleNamespace(correlation_id=invocation.correlation_id),
            joined_decision=SimpleNamespace(scope="scope"),
            issued_at=datetime.now(UTC) - timedelta(seconds=1),
            expires_at=binding.expires_at,
            authenticated_identity_ref=ACTOR_REF,
        )
        return SimpleNamespace(
            allow=True,
            state=DecisionState.ALLOW,
            code=DecisionCode.ALLOW,
            context=context,
        ), Verifier()


@pytest.fixture(autouse=True)
def typed_authorization_boundary(monkeypatch):
    def authorize(
        request,
        _bearer,
        capability,
        target,
        *,
        decision_authorizer,
        invocation_factory,
    ):
        if decision_authorizer.state is DecisionState.UNAVAILABLE:
            raise control_plane_api._PolicyUnavailable
        invocation = invocation_factory(request, capability, target)
        binding = SimpleNamespace(
            purpose=invocation.purpose,
            resource_type=invocation.resource_type,
            resource_id=invocation.resource_id,
            capability=capability,
            target=target,
            owner_policy_revision=REVISION,
        )
        return SimpleNamespace(
            binding=binding,
            boundary=invocation.boundary,
            authenticated_identity_ref=ACTOR_REF,
        ), None

    monkeypatch.setattr(control_plane_api, "_typed_context", authorize)


def _owners(primary: Owner):
    owners = {}
    for spec in COMMAND_SPECS:
        owners[(spec.owner_service, spec.owner_operation)] = (
            primary if spec.command_id == "coordination.claim-task" else Owner()
        )
    return owners


def _request(*, preview=False, version="v1", purpose="coordinate work", task="task-1"):
    return {
        "schema_version": "skdashboard-command/v1",
        "purpose": purpose,
        "resource": {"resource_type": "skcoord.task", "resource_id": task},
        "expected_version": version,
        "preview": preview,
        "arguments": {"agent_id": "agent-1"},
    }


def _client(
    tmp_path: Path,
    owner: Owner,
    *,
    authorizer=None,
    audit=None,
    timeout=None,
):
    specs = COMMAND_SPECS
    if timeout is not None:
        specs = tuple(
            type(spec)(
                **{
                    **spec.__dict__,
                    "timeout_seconds": timeout if spec.command_id == "coordination.claim-task" else spec.timeout_seconds,
                }
            )
            for spec in specs
        )
    gateway = CommandGateway(
        _owners(owner),
        audit or Audit(),
        specs=specs,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    def factory(request, capability, target):
        return SimpleNamespace(
            node_id="chiap04",
            purpose=request.headers.get("x-command-purpose", "coordinate work"),
            audience="skdashboard",
            capability=capability,
            target=target,
            resource_type="skcoord.task",
            resource_id=request.headers.get("x-command-resource", "task-1"),
            correlation_id=request.headers.get("x-request-id", "request-1"),
            boundary=RequestBoundary(
                client_kind=ClientKind.BROWSER,
                origin=ORIGIN,
                csrf_verified=True,
                idempotency_key=request.headers.get("idempotency-key"),
            ),
        )

    app = create_app(
        tmp_path,
        control_plane_decision_authorizer=authorizer or Authorizer(),
        control_plane_invocation_factory=factory,
        control_plane_command_gateway=gateway,
    )
    return TestClient(app), gateway


def _post(client, body=None, key="idem-command-0001", **headers):
    return client.post(
        "/api/v1/commands/coordination/claim-task",
        json=body or _request(),
        headers={
            "Authorization": "Bearer opaque",
            "Origin": ORIGIN,
            "Idempotency-Key": key,
            **headers,
        },
    )


def test_closed_registry_maps_one_owner_operation_and_capauth_scope() -> None:
    assert len({spec.path for spec in COMMAND_SPECS}) == len(COMMAND_SPECS)
    assert len({(spec.owner_service, spec.owner_operation) for spec in COMMAND_SPECS}) == len(COMMAND_SPECS)
    assert {spec.capability for spec in COMMAND_SPECS} == {
        "skdashboard.commands.coordination",
        "skdashboard.commands.cmdb",
        "skdashboard.commands.service_operations",
    }
    with pytest.raises(ValueError, match="exactly one"):
        CommandGateway({}, Audit(), idempotency_store=InMemoryIdempotencyStore())


def test_preview_routes_to_owner_without_effect_and_audits_no_arguments(tmp_path: Path) -> None:
    owner, audit = Owner(), Audit()
    client, _ = _client(tmp_path, owner, audit=audit)
    response = _post(client, _request(preview=True))
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["owner_result"]["status"] == "previewed"
    assert receipt["owner_result"]["external_effect_state"] == "none"
    assert receipt["actor"] == ACTOR_REF
    assert receipt["purpose"] == "coordinate work"
    assert receipt["resource"] == {"resource_type": "skcoord.task", "resource_id": "task-1"}
    assert receipt["input_hash"].startswith("sha256:")
    assert receipt["policy_revision"] == REVISION
    assert receipt["accepted_at"] <= receipt["completed_at"]
    assert owner.calls[0].preview is True
    assert owner.authorizations[0].authenticated_identity_ref == ACTOR_REF
    assert all("arguments" not in event for event in audit.events)
    assert "agent-1" not in json.dumps(audit.events)


def test_duplicate_replays_same_receipt_and_changed_input_conflicts(tmp_path: Path) -> None:
    owner = Owner()
    client, _ = _client(tmp_path, owner)
    first = _post(client)
    duplicate = _post(client)
    conflict = _post(client, _request(version="v2"))
    assert first.status_code == duplicate.status_code == 200
    assert duplicate.headers["idempotent-replay"] == "true"
    assert duplicate.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(owner.calls) == 1


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (
            OwnerCommandResult(
                status="previewed",
                result_code="PREVIEW_READY",
                owner_version="v1",
                owner_audit_ref="owner-audit:preview",
                external_effect_state="none",
            ),
            200,
        ),
        (
            OwnerCommandResult(
                status="applied",
                result_code="APPLIED",
                owner_version="v2",
                owner_audit_ref="owner-audit:applied",
                external_effect_state="occurred",
            ),
            200,
        ),
        (
            OwnerCommandResult(
                status="duplicate",
                result_code="OWNER_IDEMPOTENT_REPLAY",
                owner_version="v2",
                owner_audit_ref="owner-audit:duplicate",
                external_effect_state="none",
            ),
            200,
        ),
        (
            OwnerCommandResult(
                status="conflict",
                result_code="EXPECTED_VERSION_CONFLICT",
                owner_version="v2",
                owner_audit_ref="owner-audit:conflict",
                external_effect_state="none",
            ),
            409,
        ),
        (
            OwnerCommandResult(
                status="denied",
                result_code="STATE_MACHINE_DENIED",
                owner_audit_ref="owner-audit:denied",
                external_effect_state="none",
            ),
            403,
        ),
        (
            OwnerCommandResult(
                status="failed",
                result_code="OWNER_REJECTED",
                owner_audit_ref="owner-audit:failed",
                external_effect_state="none",
                retryable=False,
            ),
            503,
        ),
    ],
    ids=["previewed", "applied", "duplicate", "conflict", "denied", "failed"],
)
def test_terminal_receipt_replay_preserves_status_body_and_owner_call_count(
    tmp_path: Path, result: OwnerCommandResult, expected_status: int
) -> None:
    owner = Owner([result])
    client, _ = _client(tmp_path, owner)
    first = _post(client, _request(preview=result.status == "previewed"), key=f"idem-terminal-{result.status}")
    replay = _post(client, _request(preview=result.status == "previewed"), key=f"idem-terminal-{result.status}")
    assert first.status_code == replay.status_code == expected_status
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json() == first.json()
    assert len(owner.calls) == 1


def test_expected_version_conflict_and_owner_denial_are_typed(tmp_path: Path) -> None:
    owner = Owner(
        [
            OwnerCommandResult(
                status="conflict",
                result_code="EXPECTED_VERSION_CONFLICT",
                owner_version="v2",
                owner_audit_ref="owner-audit:conflict",
                external_effect_state="none",
            ),
            OwnerCommandResult(
                status="denied",
                result_code="STATE_MACHINE_DENIED",
                owner_audit_ref="owner-audit:denied",
                external_effect_state="none",
            ),
        ]
    )
    client, _ = _client(tmp_path, owner)
    conflict = _post(client, key="idem-command-0002")
    denial = _post(client, key="idem-command-0003")
    assert conflict.status_code == 409
    assert conflict.json()["owner_result"]["result_code"] == "EXPECTED_VERSION_CONFLICT"
    assert denial.status_code == 403
    assert denial.json()["owner_result"]["result_code"] == "STATE_MACHINE_DENIED"


def test_timeout_is_unknown_effect_and_safe_retry_uses_same_owner_key(tmp_path: Path) -> None:
    owner = Owner(delay=0.03)
    client, _ = _client(tmp_path, owner, timeout=0.001)
    first = _post(client, key="idem-command-0004")
    assert first.status_code == 504
    assert first.headers["retry-after"] == "1"
    assert first.json()["owner_result"] == {
        "schema_version": "owner-command-result/v1",
        "status": "failed",
        "result_code": "OWNER_TIMEOUT",
        "owner_version": None,
        "owner_audit_ref": "owner-audit:timeout-unknown",
        "external_effect_state": "unknown",
        "retryable": True,
    }
    second = _post(client, key="idem-command-0004")
    assert second.status_code == 504
    assert [call.idempotency_key for call in owner.calls] == ["idem-command-0004"] * 2
    assert [call.attempt for call in owner.calls] == [1, 2]


def test_policy_unavailable_is_503_and_owner_is_not_called(tmp_path: Path) -> None:
    owner = Owner()
    client, _ = _client(tmp_path, owner, authorizer=Authorizer(DecisionState.UNAVAILABLE))
    response = _post(client, key="idem-command-0005")
    assert response.status_code == 503
    assert response.json()["code"] == "POLICY_UNAVAILABLE"
    assert response.json()["retryable"] is True
    assert owner.calls == []


def test_capauth_denial_is_sanitized_and_owner_is_not_called(
    tmp_path: Path, monkeypatch
) -> None:
    owner = Owner()
    client, _ = _client(tmp_path, owner)
    monkeypatch.setattr(control_plane_api, "_typed_context", lambda *_args, **_kwargs: None)
    response = _post(client, key="idem-command-0011")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert owner.calls == []


def test_browser_mutation_requires_csrf_before_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.undo()
    owner = Owner()
    client, _ = _client(tmp_path, owner)
    response = _post(client, key="idem-command-0014")
    assert response.status_code == 403
    assert owner.calls == []


def test_owner_duplicate_receipt_is_typed_without_second_effect(tmp_path: Path) -> None:
    owner = Owner(
        [
            OwnerCommandResult(
                status="duplicate",
                result_code="OWNER_IDEMPOTENT_REPLAY",
                owner_version="v2",
                owner_audit_ref="owner-audit:duplicate",
                external_effect_state="none",
            )
        ]
    )
    client, _ = _client(tmp_path, owner)
    response = _post(client, key="idem-command-0012")
    assert response.status_code == 200
    assert response.json()["owner_result"]["status"] == "duplicate"
    assert response.json()["owner_result"]["result_code"] == "OWNER_IDEMPOTENT_REPLAY"


def test_capability_resource_purpose_and_idempotency_bindings_fail_closed(tmp_path: Path) -> None:
    owner = Owner()
    client, _ = _client(tmp_path, owner)
    wrong_purpose = _post(
        client,
        key="idem-command-0006",
        **{"X-Command-Purpose": "different purpose"},
    )
    wrong_resource = _post(
        client,
        key="idem-command-0007",
        **{"X-Command-Resource": "task-2"},
    )
    missing_key = _post(client, key="short")
    assert wrong_purpose.status_code == wrong_resource.status_code == 403
    assert missing_key.status_code == 403
    assert owner.calls == []


def test_closed_routes_reject_generic_proxy_shell_filesystem_and_arbitrary_url(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, Owner())
    for path in (
        "/api/v1/commands/shell",
        "/api/v1/commands/filesystem",
        "/api/v1/commands/proxy",
        "/api/v1/commands/https://example.test",
        "/api/v1/commands/coordination/arbitrary",
    ):
        assert client.post(path).status_code == 404
    payload = _request()
    payload["arguments"] = {"agent_id": "agent-1", "url": "https://example.test"}
    assert _post(client, payload, key="idem-command-0008").status_code == 400


def test_audit_failure_blocks_owner_and_jsonl_chain_is_sanitized(tmp_path: Path) -> None:
    class BrokenAudit:
        def append(self, _event):
            raise OSError("offline")

    owner = Owner()
    client, _ = _client(tmp_path, owner, audit=BrokenAudit())
    response = _post(client, key="idem-command-0009")
    assert response.status_code == 503
    assert response.json()["code"] == "AUDIT_UNAVAILABLE"
    assert owner.calls == []

    audit = JsonlCommandAudit(tmp_path / "audit" / "commands.jsonl")
    first = audit.append({"event": "one", "input_hash": "sha256:" + "1" * 64})
    second = audit.append({"event": "two", "input_hash": "sha256:" + "2" * 64})
    records = [json.loads(line) for line in audit.path.read_text().splitlines()]
    assert first == "sha256:" + records[0]["record_hash"]
    assert second == "sha256:" + records[1]["record_hash"]
    assert records[1]["previous_hash"] == records[0]["record_hash"]
    assert audit.path.stat().st_mode & 0o077 == 0


def test_idempotency_store_unavailable_fails_before_owner(tmp_path: Path) -> None:
    class BrokenStore:
        def begin(self, *_args):
            raise OSError("offline")

        def finish(self, *_args):
            raise AssertionError("not reached")

    owner = Owner()
    gateway = CommandGateway(
        _owners(owner),
        Audit(),
        idempotency_store=BrokenStore(),
    )
    assert isinstance(IdempotencyClaim("pending", object()), IdempotencyClaim)

    def factory(request, capability, target):
        return SimpleNamespace(
            node_id="chiap04",
            purpose="coordinate work",
            audience="skdashboard",
            capability=capability,
            target=target,
            resource_type="skcoord.task",
            resource_id="task-1",
            correlation_id="request-1",
            boundary=RequestBoundary(
                client_kind=ClientKind.BROWSER,
                origin=ORIGIN,
                csrf_verified=True,
                idempotency_key=request.headers.get("idempotency-key"),
            ),
        )

    client = TestClient(
        create_app(
            tmp_path,
            control_plane_decision_authorizer=Authorizer(),
            control_plane_invocation_factory=factory,
            control_plane_command_gateway=gateway,
        )
    )
    response = _post(client, key="idem-command-0013")
    assert response.status_code == 503
    assert response.json()["code"] == "IDEMPOTENCY_UNAVAILABLE"
    assert owner.calls == []


def test_owner_cannot_turn_preview_into_mutation_or_bypass_state_machine(tmp_path: Path) -> None:
    owner = Owner(
        [
            OwnerCommandResult(
                status="applied",
                result_code="APPLIED",
                owner_version="v2",
                owner_audit_ref="owner-audit:unsafe",
                external_effect_state="occurred",
            )
        ]
    )
    client, _ = _client(tmp_path, owner)
    response = _post(client, _request(preview=True), key="idem-command-0010")
    assert response.status_code == 503
    assert response.json()["owner_result"]["result_code"] == "OWNER_UNAVAILABLE"
