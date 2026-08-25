"""Closed, owner-routed control-plane command boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.responses import JSONResponse
from starlette.routing import Route

MAX_COMMAND_BYTES = 32 * 1024
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,255}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CommandResource(_StrictModel):
    resource_type: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=256)

    @field_validator("resource_type", "resource_id")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if not _SAFE_ID_RE.fullmatch(value):
            raise ValueError("resource identifiers contain unsupported characters")
        return value


class CommandRequest(_StrictModel):
    schema_version: Literal["skdashboard-command/v1"] = "skdashboard-command/v1"
    purpose: str = Field(min_length=1, max_length=256)
    resource: CommandResource
    expected_version: str = Field(min_length=1, max_length=256)
    preview: bool
    arguments: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("purpose", "expected_version")
    @classmethod
    def no_surrounding_space(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("command fields cannot have surrounding whitespace")
        return value

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value: dict[str, str | int | bool]):
        if len(value) > 16:
            raise ValueError("too many command arguments")
        if any(not _SAFE_ID_RE.fullmatch(key) for key in value):
            raise ValueError("argument name contains unsupported characters")
        if any(isinstance(item, str) and (not item or len(item) > 1024) for item in value.values()):
            raise ValueError("argument string is empty or too long")
        return value


class OwnerCommandEnvelope(_StrictModel):
    schema_version: Literal["skdashboard-owner-command/v1"] = (
        "skdashboard-owner-command/v1"
    )
    command_id: str
    owner_service: str
    owner_operation: str
    actor: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    purpose: str
    resource: CommandResource
    expected_version: str
    preview: bool
    arguments: dict[str, str | int | bool]
    idempotency_key: str
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt: int = Field(ge=1, le=100)


class OwnerCommandResult(_StrictModel):
    schema_version: Literal["owner-command-result/v1"] = "owner-command-result/v1"
    status: Literal["previewed", "applied", "duplicate", "conflict", "denied", "failed"]
    result_code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z0-9_]+$")
    owner_version: str | None = Field(default=None, max_length=256)
    owner_audit_ref: str = Field(min_length=1, max_length=256)
    external_effect_state: Literal["none", "pending", "occurred", "unknown"]
    retryable: bool = False

    @field_validator("owner_version", "owner_audit_ref")
    @classmethod
    def safe_owner_reference(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID_RE.fullmatch(value):
            raise ValueError("owner references contain unsupported characters")
        return value

    @model_validator(mode="after")
    def preserve_gates(self):
        if self.status == "previewed" and self.external_effect_state != "none":
            raise ValueError("preview cannot report an external effect")
        if self.status in {"conflict", "denied"} and self.retryable:
            raise ValueError("conflict and denial are not retryable")
        return self


class CommandReceipt(_StrictModel):
    schema_version: Literal["skdashboard-command-receipt/v1"] = (
        "skdashboard-command-receipt/v1"
    )
    receipt_id: str
    command_id: str
    owner_service: str
    owner_operation: str
    actor: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    purpose: str
    resource: CommandResource
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_result: OwnerCommandResult
    accepted_at: datetime
    completed_at: datetime
    attempt: int = Field(ge=1, le=100)


class CommandOwner(Protocol):
    """Authoritative owner API, including owner policy, state machine, and audit."""

    async def invoke(
        self,
        command: OwnerCommandEnvelope,
        authorization,
    ) -> OwnerCommandResult: ...


class CommandAudit(Protocol):
    """Append-only receipt audit. It never receives command arguments."""

    def append(self, event: dict[str, object]) -> str: ...


class IdempotencyStore(Protocol):
    """Atomic compare-and-store seam for deployment-owned durable replay state."""

    def begin(self, command_id: str, key: str, input_hash: str) -> "IdempotencyClaim": ...

    def finish(
        self,
        token: object,
        receipt: CommandReceipt | None,
        retryable: bool,
    ) -> None: ...


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    path: str
    owner_service: str
    owner_operation: str
    capability: str
    resource_type: str
    argument_names: frozenset[str]
    timeout_seconds: float = 10.0
    safe_retry_after_timeout: bool = True

    def __post_init__(self) -> None:
        values = (
            self.command_id,
            self.owner_service,
            self.owner_operation,
            self.resource_type,
        )
        if any(not _SAFE_ID_RE.fullmatch(value) for value in values):
            raise ValueError("command specification contains an unsafe identifier")
        if not self.path.startswith("/api/v1/commands/") or "{" in self.path:
            raise ValueError("command path must be one exact non-parameterized v1 route")
        if self.capability not in {
            "skdashboard.commands.coordination",
            "skdashboard.commands.cmdb",
            "skdashboard.commands.service_operations",
        }:
            raise ValueError("command capability is outside the CapAuth contract")
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError("command timeout is outside the bounded contract")


COMMAND_SPECS = (
    CommandSpec(
        command_id="coordination.claim-task",
        path="/api/v1/commands/coordination/claim-task",
        owner_service="skcoord",
        owner_operation="claim_task",
        capability="skdashboard.commands.coordination",
        resource_type="skcoord.task",
        argument_names=frozenset({"agent_id"}),
    ),
    CommandSpec(
        command_id="cmdb.reconcile-node",
        path="/api/v1/commands/cmdb/reconcile-node",
        owner_service="skcapstone-cmdb",
        owner_operation="reconcile_node",
        capability="skdashboard.commands.cmdb",
        resource_type="cmdb.node",
        argument_names=frozenset({"observation_id"}),
    ),
    CommandSpec(
        command_id="service-operations.request-change",
        path="/api/v1/commands/service-operations/request-change",
        owner_service="skcapstone-itil",
        owner_operation="request_service_change",
        capability="skdashboard.commands.service_operations",
        resource_type="itil.service",
        argument_names=frozenset({"change_id", "approval_id"}),
    ),
)


class JsonlCommandAudit:
    """Hash-chained, append-only JSONL audit for sanitized command evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def append(self, event: dict[str, object]) -> str:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a+b", buffering=0) as stream:
            os.chmod(self.path, 0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0)
            lines = stream.read().splitlines()
            previous = "0" * 64
            if lines:
                prior = json.loads(lines[-1])
                previous = prior["record_hash"]
            record = {"previous_hash": previous, **event}
            record_hash = hashlib.sha256(_json(record)).hexdigest()
            stream.seek(0, os.SEEK_END)
            stream.write(_json({**record, "record_hash": record_hash}) + b"\n")
            os.fsync(stream.fileno())
            return f"sha256:{record_hash}"


@dataclass
class _IdempotencyState:
    input_hash: str
    attempt: int = 0
    pending: bool = False
    receipt: CommandReceipt | None = None
    retryable: bool = False


@dataclass(frozen=True)
class IdempotencyClaim:
    disposition: Literal["started", "replay", "pending", "conflict"]
    token: object
    attempt: int = 0
    receipt: CommandReceipt | None = None


class InMemoryIdempotencyStore:
    """Process-local store for tests and isolated development only."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[tuple[str, str], _IdempotencyState] = {}

    def begin(self, command_id: str, key: str, input_hash: str):
        with self._lock:
            state = self._states.get((command_id, key))
            if state is not None and state.input_hash != input_hash:
                return IdempotencyClaim("conflict", state)
            if state is not None and state.receipt is not None and not state.retryable:
                return IdempotencyClaim("replay", state, state.attempt, state.receipt)
            if state is not None and state.pending:
                return IdempotencyClaim("pending", state, state.attempt)
            state = state or _IdempotencyState(input_hash=input_hash)
            state.attempt += 1
            state.pending = True
            self._states[(command_id, key)] = state
            return IdempotencyClaim("started", state, state.attempt)

    def finish(
        self,
        token: object,
        receipt: CommandReceipt | None,
        retryable: bool,
    ) -> None:
        if type(token) is not _IdempotencyState:
            raise TypeError("unknown idempotency token")
        with self._lock:
            token.pending = False
            token.receipt = receipt
            token.retryable = retryable


class CommandGateway:
    """Validate, authorize upstream, and route only closed commands to owners."""

    def __init__(
        self,
        owners: dict[tuple[str, str], CommandOwner],
        audit: CommandAudit,
        *,
        specs: tuple[CommandSpec, ...] = COMMAND_SPECS,
        idempotency_store: IdempotencyStore | None = None,
        clock=_utc_now,
    ) -> None:
        if len({spec.path for spec in specs}) != len(specs):
            raise ValueError("command routes must be unique")
        if len({spec.command_id for spec in specs}) != len(specs):
            raise ValueError("command identifiers must be unique")
        expected = {(spec.owner_service, spec.owner_operation) for spec in specs}
        if set(owners) != expected:
            raise ValueError("each command requires exactly one owning service operation")
        self.specs = specs
        self.owners = dict(owners)
        self.audit = audit
        self.clock = clock
        if idempotency_store is None:
            raise ValueError("command routes require an injected atomic idempotency store")
        self.idempotency_store = idempotency_store

    def routes(self, protect) -> list[Route]:
        routes = []
        for spec in self.specs:
            async def handler(request, command_spec=spec):
                return await self.handle(request, command_spec)

            routes.append(
                Route(spec.path, protect(handler, spec.capability), methods=["POST"])
            )
        return routes

    async def handle(self, request, spec: CommandSpec):
        idempotency_key = request.headers.get("idempotency-key", "")
        if not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
            return self._error(400, "INVALID_IDEMPOTENCY_KEY", False)
        try:
            body = await request.body()
            if not body or len(body) > MAX_COMMAND_BYTES:
                raise ValueError("command body is empty or too large")
            payload = json.loads(body, object_pairs_hook=self._closed_object)
            command = CommandRequest.model_validate(payload)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return self._error(400, "INVALID_COMMAND", False)
        if (
            command.resource.resource_type != spec.resource_type
            or set(command.arguments) != spec.argument_names
        ):
            return self._error(400, "COMMAND_CONTRACT_MISMATCH", False)

        context = request.state.control_plane_decision
        binding = context.binding
        if (
            binding.purpose != command.purpose
            or binding.resource_type != command.resource.resource_type
            or binding.resource_id != command.resource.resource_id
            or binding.capability != spec.capability
            or binding.target != spec.path
            or context.boundary.idempotency_key != idempotency_key
        ):
            return self._error(403, "COMMAND_BINDING_MISMATCH", False)

        input_hash = "sha256:" + hashlib.sha256(
            _json({"command_id": spec.command_id, "request": command.model_dump(mode="json")})
        ).hexdigest()
        try:
            claim = self.idempotency_store.begin(
                spec.command_id,
                idempotency_key,
                input_hash,
            )
        except Exception:
            return self._error(503, "IDEMPOTENCY_UNAVAILABLE", True)
        if type(claim) is not IdempotencyClaim:
            return self._error(503, "IDEMPOTENCY_UNAVAILABLE", True)
        if claim.disposition == "conflict":
            return self._error(409, "IDEMPOTENCY_CONFLICT", False)
        if claim.disposition == "replay" and claim.receipt is not None:
            response = JSONResponse(
                claim.receipt.model_dump(mode="json"),
                status_code=self._owner_result_status(claim.receipt.owner_result),
            )
            response.headers["Idempotent-Replay"] = "true"
            return response
        if claim.disposition == "pending":
            return self._error(409, "COMMAND_IN_PROGRESS", True)
        if claim.disposition != "started" or claim.attempt < 1:
            return self._error(503, "IDEMPOTENCY_UNAVAILABLE", True)

        accepted_at = self.clock()
        try:
            self.audit.append(
                {
                    "event": "command.accepted",
                    "at": accepted_at.isoformat(),
                    "command_id": spec.command_id,
                    "actor_ref": context.authenticated_identity_ref,
                    "purpose": command.purpose,
                    "resource_type": command.resource.resource_type,
                    "resource_id": command.resource.resource_id,
                    "input_hash": input_hash,
                    "policy_revision": binding.owner_policy_revision,
                    "attempt": claim.attempt,
                }
            )
        except Exception:
            self.idempotency_store.finish(claim.token, None, True)
            return self._error(503, "AUDIT_UNAVAILABLE", True)

        envelope = OwnerCommandEnvelope(
            command_id=spec.command_id,
            owner_service=spec.owner_service,
            owner_operation=spec.owner_operation,
            actor=context.authenticated_identity_ref,
            purpose=command.purpose,
            resource=command.resource,
            expected_version=command.expected_version,
            preview=command.preview,
            arguments=command.arguments,
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            policy_revision=binding.owner_policy_revision,
            attempt=claim.attempt,
        )
        try:
            raw_result = await asyncio.wait_for(
                self.owners[(spec.owner_service, spec.owner_operation)].invoke(
                    envelope,
                    context,
                ),
                timeout=spec.timeout_seconds,
            )
            result = OwnerCommandResult.model_validate(raw_result)
            if command.preview and result.status != "previewed":
                raise ValueError("owner attempted a non-preview result")
        except asyncio.TimeoutError:
            result = OwnerCommandResult(
                status="failed",
                result_code="OWNER_TIMEOUT",
                owner_audit_ref="owner-audit:timeout-unknown",
                external_effect_state="unknown",
                retryable=spec.safe_retry_after_timeout,
            )
        except Exception:
            result = OwnerCommandResult(
                status="failed",
                result_code="OWNER_UNAVAILABLE",
                owner_audit_ref="owner-audit:unavailable",
                external_effect_state="unknown",
                retryable=True,
            )

        receipt = CommandReceipt(
            receipt_id=f"cmdr-{uuid4().hex}",
            command_id=spec.command_id,
            owner_service=spec.owner_service,
            owner_operation=spec.owner_operation,
            actor=context.authenticated_identity_ref,
            purpose=command.purpose,
            resource=command.resource,
            input_hash=input_hash,
            policy_revision=binding.owner_policy_revision,
            owner_result=result,
            accepted_at=accepted_at,
            completed_at=self.clock(),
            attempt=claim.attempt,
        )
        try:
            self.audit.append(
                {
                    "event": "command.completed",
                    "at": receipt.completed_at.isoformat(),
                    "receipt": receipt.model_dump(mode="json"),
                }
            )
        except Exception:
            self.idempotency_store.finish(claim.token, None, True)
            return self._error(503, "AUDIT_UNAVAILABLE", True)

        retryable = result.status == "failed" and result.retryable
        try:
            self.idempotency_store.finish(claim.token, receipt, retryable)
        except Exception:
            return self._error(503, "IDEMPOTENCY_UNAVAILABLE", True)
        status = self._owner_result_status(result)
        response = JSONResponse(receipt.model_dump(mode="json"), status_code=status)
        if retryable:
            response.headers["Retry-After"] = "1"
        return response

    @staticmethod
    def _owner_result_status(result: OwnerCommandResult) -> int:
        return {
            "conflict": 409,
            "denied": 403,
            "failed": 504 if result.result_code == "OWNER_TIMEOUT" else 503,
        }.get(result.status, 200)

    @staticmethod
    def _closed_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    @staticmethod
    def _error(status: int, code: str, retryable: bool):
        return JSONResponse(
            {"code": code, "message": "command request was not accepted", "retryable": retryable},
            status_code=status,
        )


__all__ = [
    "COMMAND_SPECS",
    "CommandGateway",
    "CommandReceipt",
    "CommandRequest",
    "CommandResource",
    "CommandSpec",
    "JsonlCommandAudit",
    "InMemoryIdempotencyStore",
    "IdempotencyClaim",
    "OwnerCommandEnvelope",
    "OwnerCommandResult",
]
