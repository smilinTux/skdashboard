"""Production-safe composition for the authenticated read-only control plane.

Signing stays behind the injected host-local credential signer. Request-bound
capability material remains inside the in-process issuer factory.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from capauth import (
    CapabilityIssuer,
    ClientKind,
    ControlPlaneDecisionAuthorizer,
    ControlPlaneInvocationV1,
    CurrentPolicyRevisions,
    DashboardIssuerAuthorizationConfigV1,
    DelegatedCapabilityIssuance,
    InProcessIssuerFactory,
    InProcessIssuerRequest,
    OperatorSessionManager,
    Principal,
)
from capauth.control_plane import RequestBoundary
from skcoord.authorized_card_policy import (
    AuthorizedCardPolicyBackend,
    AuthorizedCardPolicyProvider,
    FileAuthorizedCardPolicyBackend,
)

from .control_plane_api import ALLOWED_BROWSER_ORIGINS
from .dashboard_schedule import ScheduleProjectionProvider

NODE_ID = "chiap08"
PURPOSE = "project-management-reporting"
AUDIENCE = "skdashboard"
CAPABILITY = "skdashboard.read"
TARGET = "/api/v1/overview"
SCHEDULE_TARGET = "/api/v1/schedule/projection"
AUTHENTICATED_TARGETS = frozenset({TARGET, SCHEDULE_TARGET})
RESOURCE_TYPE = "skcoord.card_store.project_snapshot"
MAX_OPERATOR_PROOFS = 1024


@dataclass(frozen=True)
class LiveControlPlaneConfig:
    """Non-secret exact bindings for one deployed read-only composition."""

    legacy_board_url: str
    resource_id: str
    owner_policy_revision: str
    tenant_id: str
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
        if (
            not isinstance(self.tenant_id, str)
            or not 1 <= len(self.tenant_id) <= 128
            or not self.tenant_id.isascii()
            or any(not (character.isalnum() or character in "._-") for character in self.tenant_id)
        ):
            raise ValueError("an exact tenant identifier is required")
        if not 1 <= self.capability_ttl_seconds <= 300:
            raise ValueError("capability TTL must be between 1 and 300 seconds")


@dataclass(frozen=True)
class LiveControlPlaneComposition:
    """The exact components injected into the read-only application."""

    decision_authorizer: ControlPlaneDecisionAuthorizer
    invocation_factory: object
    project_provider: AuthorizedCardPolicyProvider
    schedule_provider: ScheduleProjectionProvider
    session_authorizer: object | None
    legacy_board_url: str


class _InjectedCapabilityIssuer:
    """Keep request-bound capability material inside the in-process factory."""

    __slots__ = ("_issuer",)

    def __init__(self, signer, *, clock=None) -> None:
        self._issuer = CapabilityIssuer(signer, clock=clock)

    def issue(self, request: DelegatedCapabilityIssuance):
        if type(request) is not DelegatedCapabilityIssuance:
            raise TypeError("a typed delegated issuance request is required")
        return self._issuer.issue_root(
            principal=request.principal,
            scope=request.scope,
            ttl_seconds=request.ttl_seconds,
            max_delegation_depth=0,
        )


class _UnavailableSessionAuthorizer:
    """Keep incomplete compositions fail closed without a bearer fallback."""

    def __call__(self, *_args, **_kwargs):
        return None


class InProcessOperatorBridge:
    """Bind a validated browser login to atomic in-process authorization."""

    __slots__ = ("_config", "_factories", "_proofs", "_revisions", "_sessions")

    def __init__(
        self,
        *,
        config: LiveControlPlaneConfig,
        sessions: OperatorSessionManager,
        revisions: CurrentPolicyRevisions,
        signer,
        clock=None,
    ) -> None:
        issuer = _InjectedCapabilityIssuer(signer, clock=clock)
        self._config = config
        self._proofs: dict[str, tuple[str, str, str]] = {}
        self._sessions = sessions
        self._revisions = revisions
        self._factories = {
            (origin, target): InProcessIssuerFactory(
                sessions=sessions,
                config=DashboardIssuerAuthorizationConfigV1(
                    allowed_origin=origin,
                    node_id=NODE_ID,
                    purpose=PURPOSE,
                    capability=CAPABILITY,
                    operation="read",
                    target=target,
                    resource_type=RESOURCE_TYPE,
                    resource_id=config.resource_id,
                    owner_policy_revision=config.owner_policy_revision,
                    ttl_seconds=config.capability_ttl_seconds,
                ),
                issuer=issuer,
                enabled=True,
                clock=clock,
            )
            for origin in ALLOWED_BROWSER_ORIGINS
            for target in AUTHENTICATED_TARGETS
        }

    def enroll(self, subject: str, origin: str) -> str:
        if (
            not isinstance(subject, str)
            or not subject.isascii()
            or not 1 <= len(subject) <= 256
            or subject != subject.strip()
            or origin not in ALLOWED_BROWSER_ORIGINS
        ):
            raise PermissionError("validated operator subject is unavailable")
        principal = Principal(principal_id=subject, subject=subject, kind="human")
        device = secrets.token_urlsafe(32)
        material = self._sessions.create(
            principal=principal,
            acting_principal=principal,
            device_fingerprint=device,
            capability_ceiling=(CAPABILITY,),
            purpose=PURPOSE,
            allowed_origin=origin,
            revisions=self._revisions,
            ttl_seconds=8 * 60 * 60,
            idle_seconds=30 * 60,
        )
        cookie, csrf = material.take()
        if len(self._proofs) >= MAX_OPERATOR_PROOFS:
            self._proofs.pop(next(iter(self._proofs)))
        handle = secrets.token_urlsafe(32)
        self._proofs[handle] = (cookie, csrf, device)
        return handle

    def request(self, handle: str, request) -> InProcessIssuerRequest:
        if not isinstance(handle, str) or not handle.isascii():
            raise PermissionError("operator session material is unavailable")
        proof = self._proofs.get(handle)
        if proof is None:
            raise PermissionError("operator session material is unavailable")
        cookie, csrf, device = proof
        nonce = request.headers.get("x-request-id", "")[:256]
        if len(nonce) < 16:
            nonce = secrets.token_hex(16)
        return InProcessIssuerRequest(
            session_cookie=cookie,
            csrf_token=csrf,
            request_nonce=nonce,
            device_fingerprint=device,
        )

    def discard(self, handle: str) -> None:
        if isinstance(handle, str):
            self._proofs.pop(handle, None)

    def __call__(
        self,
        request,
        session,
        capability,
        target,
        authorizer,
        invocation_factory,
    ):
        if capability != CAPABILITY or target not in AUTHENTICATED_TARGETS:
            return None
        proof = getattr(session, "control_plane_request", None)
        if not isinstance(proof, InProcessIssuerRequest):
            return None
        origin = request.headers.get("origin")
        factory = self._factories.get((origin, target))
        if factory is None:
            return None
        invocation = invocation_factory(request, capability, target)
        result, verifier = factory.authorize(proof, authorizer, invocation)
        if not result.allow or result.context is None or verifier is None:
            if verifier is not None:
                verifier.close()
            return None
        return result.context, verifier


class _UnavailableScheduleSource:
    """Keep Schedule fail closed until a canonical owner source is deployed."""

    def read(self, _context, _request, _home):
        return None


class _LiveOwnerPolicyProvider:
    """Reuse the exact project owner policy for the unavailable Schedule seam."""

    __slots__ = ("_project_provider",)

    def __init__(self, project_provider: AuthorizedCardPolicyProvider) -> None:
        self._project_provider = project_provider

    def decide(self, binding, capauth_decision):
        if binding.target == TARGET:
            return self._project_provider.decide(binding, capauth_decision)
        if binding.target != SCHEDULE_TARGET:
            return None
        # CapAuth already approved the actual Schedule target. These copies are
        # used only to select the same exact resource owner entry. The returned
        # decision is still joined against the original Schedule binding.
        overview_binding = binding.model_copy(update={"target": TARGET})
        overview_decision = capauth_decision.model_copy(
            update={"scope": overview_binding.capability_scope()}
        )
        return self._project_provider.decide(overview_binding, overview_decision)


def compose_file_backed_live_control_plane(
    *,
    config: LiveControlPlaneConfig,
    capability_authorizer,
    owner_policy_file: Path,
    store_factory,
    expected_policy_uid: int | None = None,
    credential_signer=None,
    operator_sessions: OperatorSessionManager | None = None,
    operator_revisions: CurrentPolicyRevisions | None = None,
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
        credential_signer=credential_signer,
        operator_sessions=operator_sessions,
        operator_revisions=operator_revisions,
        clock=clock,
    )


def compose_live_control_plane(
    *,
    config: LiveControlPlaneConfig,
    capability_authorizer,
    owner_policy_backend: AuthorizedCardPolicyBackend,
    store_factory,
    credential_signer=None,
    operator_sessions: OperatorSessionManager | None = None,
    operator_revisions: CurrentPolicyRevisions | None = None,
    clock=None,
) -> LiveControlPlaneComposition:
    """Compose CapAuth and SKCoord around one durable owner-provider instance."""

    provider_options = {"store_factory": store_factory}
    if clock is not None:
        provider_options["clock"] = clock
    provider = AuthorizedCardPolicyProvider(owner_policy_backend, **provider_options)
    live_owner_policy = _LiveOwnerPolicyProvider(provider)
    authorizer_options = {
        "capability_authorizer": capability_authorizer,
        "owner_policy": live_owner_policy,
        "allowed_origins": ALLOWED_BROWSER_ORIGINS,
    }
    if clock is not None:
        authorizer_options["clock"] = clock
    authorizer = ControlPlaneDecisionAuthorizer(**authorizer_options)
    schedule_options = {"tenant_id": config.tenant_id}
    if clock is not None:
        schedule_options["clock"] = clock
    schedule_provider = ScheduleProjectionProvider(
        _UnavailableScheduleSource(),
        **schedule_options,
    )

    def invocation_factory(request, capability: str, target: str) -> ControlPlaneInvocationV1:
        if (
            capability != CAPABILITY
            or target not in AUTHENTICATED_TARGETS
            or request.url.path != target
        ):
            raise PermissionError("control-plane invocation is outside the exact binding")
        origin = request.headers.get("origin")
        if origin not in ALLOWED_BROWSER_ORIGINS:
            raise PermissionError("control-plane invocation origin is not approved")
        return ControlPlaneInvocationV1(
            node_id=NODE_ID,
            purpose=PURPOSE,
            audience=AUDIENCE,
            capability=CAPABILITY,
            target=target,
            resource_type=RESOURCE_TYPE,
            resource_id=config.resource_id,
            correlation_id=request.headers.get("x-request-id", "")[:128] or uuid4().hex,
            boundary=RequestBoundary(client_kind=ClientKind.BROWSER, origin=origin),
        )

    in_process_values = (credential_signer, operator_sessions, operator_revisions)
    if any(value is not None for value in in_process_values) and not all(
        value is not None for value in in_process_values
    ):
        raise ValueError("in-process authorization components must be supplied together")
    session_authorizer = _UnavailableSessionAuthorizer()
    if all(value is not None for value in in_process_values):
        session_authorizer = InProcessOperatorBridge(
            config=config,
            sessions=operator_sessions,
            revisions=operator_revisions,
            signer=credential_signer,
            clock=clock,
        )

    return LiveControlPlaneComposition(
        decision_authorizer=authorizer,
        invocation_factory=invocation_factory,
        project_provider=provider,
        schedule_provider=schedule_provider,
        session_authorizer=session_authorizer,
        legacy_board_url=config.legacy_board_url,
    )


__all__ = [
    "AUDIENCE",
    "AUTHENTICATED_TARGETS",
    "CAPABILITY",
    "LiveControlPlaneComposition",
    "LiveControlPlaneConfig",
    "InProcessOperatorBridge",
    "NODE_ID",
    "PURPOSE",
    "RESOURCE_TYPE",
    "SCHEDULE_TARGET",
    "TARGET",
    "compose_file_backed_live_control_plane",
    "compose_live_control_plane",
]
