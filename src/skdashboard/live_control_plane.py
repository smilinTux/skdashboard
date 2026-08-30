"""Production-safe composition for the authenticated read-only control plane.

This module deliberately contains no signing implementation.  It can only ask an
independently administered Unix-socket issuer for one request-bound capability.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from capauth import ClientKind, ControlPlaneDecisionAuthorizer, ControlPlaneInvocationV1
from capauth.control_plane import RequestBoundary
from skcoord.authorized_card_policy import (
    AuthorizedCardPolicyBackend,
    AuthorizedCardPolicyProvider,
    FileAuthorizedCardPolicyBackend,
)

from .control_plane_api import ALLOWED_BROWSER_ORIGINS, MAX_BEARER_BYTES

NODE_ID = "chiap04"
PURPOSE = "project-management-reporting"
AUDIENCE = "skdashboard"
CAPABILITY = "skdashboard.read"
TARGET = "/api/v1/overview"
RESOURCE_TYPE = "skcoord.card_store.project_snapshot"
MAX_ISSUER_MESSAGE_BYTES = MAX_BEARER_BYTES + 16 * 1024


@dataclass(frozen=True)
class LiveControlPlaneConfig:
    """Non-secret exact bindings for one deployed read-only composition."""

    issuer_socket: Path
    legacy_board_url: str
    resource_id: str
    owner_policy_revision: str
    issuer_uid: int = os.getuid()
    capability_ttl_seconds: int = 300

    def __post_init__(self) -> None:
        board = urlsplit(self.legacy_board_url)
        if (
            board.scheme != "https"
            or not board.netloc
            or board.username is not None
            or board.password is not None
            or board.query
            or board.fragment
        ):
            raise ValueError("legacy board URL must be a credential-free exact HTTPS URL")
        if not self.resource_id.startswith("authorized-card-set:sha256:"):
            raise ValueError("an exact authorized-card resource binding is required")
        if len(self.owner_policy_revision) != 64 or any(
            character not in "0123456789abcdef" for character in self.owner_policy_revision
        ):
            raise ValueError("an exact owner policy revision is required")
        if not 1 <= self.capability_ttl_seconds <= 300:
            raise ValueError("capability TTL must be between 1 and 300 seconds")


class EphemeralIssuerClient:
    """Request one bearer over a permission-restricted local Unix socket.

    The client has no credential cache and returns no object capable of signing.
    One invocation opens one connection, sends one bounded request, receives one
    bounded response, closes the connection, and forgets the response after its
    caller finishes authorization.
    """

    __slots__ = ("_config",)

    def __init__(self, config: LiveControlPlaneConfig) -> None:
        self._config = config

    def _validate_socket(self) -> None:
        status = self._config.issuer_socket.stat(follow_symlinks=False)
        if not stat.S_ISSOCK(status.st_mode):
            raise ConnectionError("issuer channel is unavailable")
        if status.st_uid != self._config.issuer_uid or status.st_mode & 0o007:
            raise PermissionError("issuer channel permissions are invalid")

    async def __call__(self, request, session, capability: str, target: str) -> str:
        if capability != CAPABILITY or target != TARGET:
            raise PermissionError("issuer request binding is not approved")
        if request.method != "GET" or request.url.path != TARGET:
            raise PermissionError("issuer request method or target is not approved")
        origin = request.headers.get("origin")
        if origin not in ALLOWED_BROWSER_ORIGINS:
            raise PermissionError("issuer request origin is not approved")
        credential = getattr(session, "access_token", None)
        if not isinstance(credential, str) or not credential:
            raise PermissionError("authenticated session credential is unavailable")
        self._validate_socket()
        payload = {
            "schema_version": "skdashboard-issuer-request/v1",
            "request_id": request.headers.get("x-request-id", "")[:128] or uuid4().hex,
            "session_credential": credential,
            "node_id": NODE_ID,
            "purpose": PURPOSE,
            "audience": AUDIENCE,
            "capability": CAPABILITY,
            "target": TARGET,
            "resource_type": RESOURCE_TYPE,
            "resource_id": self._config.resource_id,
            "owner_policy_revision": self._config.owner_policy_revision,
            "origin": origin,
            "ttl_seconds": self._config.capability_ttl_seconds,
            "use_limit": 1,
            "store": False,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > MAX_ISSUER_MESSAGE_BYTES:
            raise ValueError("issuer request exceeds the safe bound")
        reader = writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._config.issuer_socket)), timeout=2
            )
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), timeout=2)
            response = await asyncio.wait_for(reader.readline(), timeout=2)
            if (
                not response
                or len(response) > MAX_ISSUER_MESSAGE_BYTES
                or not response.endswith(b"\n")
            ):
                raise ConnectionError("issuer response is unavailable")
            decoded = json.loads(response)
            if set(decoded) != {"schema_version", "bearer"}:
                raise ValueError("issuer response is malformed")
            bearer = decoded["bearer"]
            if (
                decoded["schema_version"] != "skdashboard-issuer-response/v1"
                or not isinstance(bearer, str)
                or not bearer
                or not bearer.isascii()
                or len(bearer.encode()) > MAX_BEARER_BYTES
                or bearer == credential
            ):
                raise ValueError("issuer response is malformed")
            return bearer
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            reader = None
            credential = None


@dataclass(frozen=True)
class LiveControlPlaneComposition:
    """The exact components injected into the read-only application."""

    decision_authorizer: ControlPlaneDecisionAuthorizer
    invocation_factory: object
    project_provider: AuthorizedCardPolicyProvider
    session_capability_issuer: EphemeralIssuerClient
    legacy_board_url: str


def compose_file_backed_live_control_plane(
    *,
    config: LiveControlPlaneConfig,
    capability_authorizer,
    owner_policy_file: Path,
    store_factory,
    expected_policy_uid: int | None = None,
    clock=None,
) -> LiveControlPlaneComposition:
    """Compose the live runtime with SKCoord's durable fail-closed backend."""

    backend_options = {}
    if expected_policy_uid is not None:
        backend_options["expected_uid"] = expected_policy_uid
    if clock is not None:
        backend_options["clock"] = clock
    backend = FileAuthorizedCardPolicyBackend(owner_policy_file, **backend_options)
    return compose_live_control_plane(
        config=config,
        capability_authorizer=capability_authorizer,
        owner_policy_backend=backend,
        store_factory=store_factory,
        clock=clock,
    )


def compose_live_control_plane(
    *,
    config: LiveControlPlaneConfig,
    capability_authorizer,
    owner_policy_backend: AuthorizedCardPolicyBackend,
    store_factory,
    clock=None,
) -> LiveControlPlaneComposition:
    """Compose CapAuth and SKCoord around one durable owner-provider instance."""

    provider_options = {"store_factory": store_factory}
    if clock is not None:
        provider_options["clock"] = clock
    provider = AuthorizedCardPolicyProvider(owner_policy_backend, **provider_options)
    authorizer_options = {
        "capability_authorizer": capability_authorizer,
        "owner_policy": provider,
        "allowed_origins": ALLOWED_BROWSER_ORIGINS,
    }
    if clock is not None:
        authorizer_options["clock"] = clock
    authorizer = ControlPlaneDecisionAuthorizer(**authorizer_options)

    def invocation_factory(request, capability: str, target: str) -> ControlPlaneInvocationV1:
        if capability != CAPABILITY or target != TARGET or request.url.path != TARGET:
            raise PermissionError("control-plane invocation is outside the exact binding")
        origin = request.headers.get("origin")
        if origin not in ALLOWED_BROWSER_ORIGINS:
            raise PermissionError("control-plane invocation origin is not approved")
        return ControlPlaneInvocationV1(
            node_id=NODE_ID,
            purpose=PURPOSE,
            audience=AUDIENCE,
            capability=CAPABILITY,
            target=TARGET,
            resource_type=RESOURCE_TYPE,
            resource_id=config.resource_id,
            correlation_id=request.headers.get("x-request-id", "")[:128] or uuid4().hex,
            boundary=RequestBoundary(client_kind=ClientKind.BROWSER, origin=origin),
        )

    return LiveControlPlaneComposition(
        decision_authorizer=authorizer,
        invocation_factory=invocation_factory,
        project_provider=provider,
        session_capability_issuer=EphemeralIssuerClient(config),
        legacy_board_url=config.legacy_board_url,
    )


__all__ = [
    "AUDIENCE",
    "CAPABILITY",
    "EphemeralIssuerClient",
    "LiveControlPlaneComposition",
    "LiveControlPlaneConfig",
    "NODE_ID",
    "PURPOSE",
    "RESOURCE_TYPE",
    "TARGET",
    "compose_file_backed_live_control_plane",
    "compose_live_control_plane",
]
