"""Production-safe composition for the authenticated read-only control plane.

Signing stays behind the injected host-local credential signer. Request-bound
capability material remains inside the in-process issuer factory.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
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
    SanitizedControlPlaneDecisionV1,
)
from capauth.control_plane import RequestBoundary
from skcoord.authorized_card_policy import (
    AuthorizedCardPolicyBackend,
    AuthorizedCardPolicyDocumentV1,
    AuthorizedCardPolicyProvider,
    AuthorizedCardPolicySelectionV1,
    FileAuthorizedCardPolicyBackend,
    StaticAuthorizedCardPolicyBackend,
)

from .control_plane_api import ALLOWED_BROWSER_ORIGINS
from .dashboard_itil import ReliabilityProjectionProvider
from .dashboard_schedule import DATE_FIELDS, ScheduleProjectionProvider, ScheduleSourceRequest

NODE_ID = "chiap08"
PURPOSE = "project-management-reporting"
AUDIENCE = "skdashboard"
CAPABILITY = "skdashboard.read"
EVENTS_CAPABILITY = "skdashboard.events.read"
TARGET = "/api/v1/overview"
SCHEDULE_TARGET = "/api/v1/schedule/projection"
RELIABILITY_TARGET = "/api/v1/reliability/projection"
BOARD_TARGET = "/api/v1/board/summary"
EVENTS_TARGET = "/api/v1/events"
FLEET_CHAT_TARGET = "/api/v1/fleet-chat"
AUTHENTICATED_BINDINGS = frozenset(
    {
        (CAPABILITY, TARGET),
        (CAPABILITY, SCHEDULE_TARGET),
        (CAPABILITY, RELIABILITY_TARGET),
        (CAPABILITY, BOARD_TARGET),
        (CAPABILITY, FLEET_CHAT_TARGET),
        (EVENTS_CAPABILITY, EVENTS_TARGET),
    }
)
AUTHENTICATED_TARGETS = frozenset(target for _capability, target in AUTHENTICATED_BINDINGS)
RESOURCE_TYPE = "skcoord.card_store.project_snapshot"
EVENTS_RESOURCE_TYPE = "tenant"
MAX_OPERATOR_PROOFS = 1024


def _approved_request_origin(request) -> str:
    """Return an explicit or exact same-origin browser base origin."""

    origin = request.headers.get("origin")
    if origin is None:
        origin = str(request.base_url).rstrip("/")
    if origin not in ALLOWED_BROWSER_ORIGINS:
        raise PermissionError("control-plane invocation origin is not approved")
    return origin


@dataclass(frozen=True)
class LiveControlPlaneConfig:
    """Non-secret exact bindings for one deployed read-only composition."""

    legacy_board_url: str
    resource_id: str
    owner_policy_revision: str
    tenant_id: str
    capability_ttl_seconds: int = 300
    node_id: str = NODE_ID

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
        if self.node_id not in {"chiap04", "chiap08"}:
            raise ValueError("an exact approved node identifier is required")
        if not 1 <= self.capability_ttl_seconds <= 300:
            raise ValueError("capability TTL must be between 1 and 300 seconds")


@dataclass(frozen=True)
class LiveControlPlaneComposition:
    """The exact components injected into the read-only application."""

    decision_authorizer: ControlPlaneDecisionAuthorizer
    invocation_factory: object
    project_provider: AuthorizedCardPolicyProvider
    schedule_provider: ScheduleProjectionProvider
    reliability_provider: ReliabilityProjectionProvider
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

    __slots__ = (
        "_config",
        "_factories",
        "_owner_policy_revisions",
        "_proof_owner_revisions",
        "_proofs",
        "_revisions",
        "_sessions",
        "_signer",
        "_clock",
    )

    def __init__(
        self,
        *,
        config: LiveControlPlaneConfig,
        sessions: OperatorSessionManager,
        revisions: CurrentPolicyRevisions,
        signer,
        owner_policy_entries=None,
        clock=None,
    ) -> None:
        self._config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._signer = signer
        self._owner_policy_revisions = (
            None if owner_policy_entries is None else dict(owner_policy_entries)
        )
        self._proofs: dict[str, dict[str, tuple[str, str, str, str]]] = {}
        self._proof_owner_revisions: dict[str, str] = {}
        self._sessions = sessions
        self._revisions = revisions
        self._factories = {}

    def _factory(self, origin: str, capability: str, target: str, owner_revision: str):
        key = (origin, capability, target, owner_revision)
        factory = self._factories.get(key)
        if factory is None:
            factory = InProcessIssuerFactory(
                sessions=self._sessions,
                config=DashboardIssuerAuthorizationConfigV1(
                    allowed_origin=origin,
                    node_id=self._config.node_id,
                    purpose=PURPOSE,
                    capability=capability,
                    operation="read",
                    target=target,
                    resource_type=(
                        EVENTS_RESOURCE_TYPE if capability == EVENTS_CAPABILITY else RESOURCE_TYPE
                    ),
                    resource_id=(
                        self._config.tenant_id
                        if capability == EVENTS_CAPABILITY
                        else self._config.resource_id
                    ),
                    owner_policy_revision=owner_revision,
                    ttl_seconds=self._config.capability_ttl_seconds,
                ),
                issuer=_InjectedCapabilityIssuer(self._signer, clock=self._clock),
                enabled=True,
                clock=self._clock,
            )
            self._factories[key] = factory
        return factory

    def enroll(self, subject: str, origin: str) -> str:
        if (
            not isinstance(subject, str)
            or not subject.isascii()
            or not 1 <= len(subject) <= 256
            or subject != subject.strip()
            or origin not in ALLOWED_BROWSER_ORIGINS
            or (
                self._owner_policy_revisions is not None
                and (
                    subject not in self._owner_policy_revisions
                    or not (
                        self._owner_policy_revisions[subject].valid_from
                        <= self._clock()
                        < self._owner_policy_revisions[subject].expires_at
                    )
                )
            )
        ):
            raise PermissionError("validated operator subject is unavailable")
        principal = Principal(principal_id=subject, subject=subject, kind="human")
        device = secrets.token_urlsafe(32)
        proofs = {}
        owner_revision = (
            self._config.owner_policy_revision
            if self._owner_policy_revisions is None
            else self._owner_policy_revisions[subject].owner_policy_revision
        )
        revisions = self._revisions.model_copy(update={"owner": owner_revision})
        for capability in (CAPABILITY, EVENTS_CAPABILITY):
            material = self._sessions.create(
                principal=principal,
                acting_principal=principal,
                device_fingerprint=device,
                capability_ceiling=(capability,),
                purpose=PURPOSE,
                allowed_origin=origin,
                revisions=revisions,
                ttl_seconds=8 * 60 * 60,
                idle_seconds=30 * 60,
            )
            cookie, csrf = material.take()
            proofs[capability] = (cookie, csrf, device, owner_revision)
        if len(self._proofs) >= MAX_OPERATOR_PROOFS:
            self._proofs.pop(next(iter(self._proofs)))
        handle = secrets.token_urlsafe(32)
        self._proofs[handle] = proofs
        return handle

    def request(self, handle: str, request) -> InProcessIssuerRequest:
        from .session_adapter import SessionReauthenticationRequired

        if not isinstance(handle, str) or not handle.isascii():
            raise PermissionError("operator session material is unavailable")
        proofs = self._proofs.get(handle)
        capability = next(
            (
                candidate
                for candidate, target in AUTHENTICATED_BINDINGS
                if target == request.url.path
            ),
            None,
        )
        if proofs is None:
            raise SessionReauthenticationRequired(
                "process-local operator proof is no longer available"
            )
        if capability is None or capability not in proofs:
            raise PermissionError("operator session material is unavailable")
        cookie, csrf, device, owner_revision = proofs[capability]
        nonce = request.headers.get("x-request-id", "")[:256]
        if len(nonce) < 16:
            nonce = secrets.token_hex(16)
        proof = InProcessIssuerRequest(
            session_cookie=cookie,
            csrf_token=csrf,
            request_nonce=nonce,
            device_fingerprint=device,
        )
        self._proof_owner_revisions[id(proof)] = owner_revision
        return proof

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
        if (capability, target) not in AUTHENTICATED_BINDINGS:
            return None
        proof = getattr(session, "control_plane_request", None)
        if not isinstance(proof, InProcessIssuerRequest):
            return None
        try:
            origin = _approved_request_origin(request)
        except PermissionError:
            return None
        owner_revision = self._proof_owner_revisions.pop(id(proof), None)
        if owner_revision is None:
            return None
        factory = self._factory(origin, capability, target, owner_revision)
        invocation = invocation_factory(request, capability, target)
        result, verifier = factory.authorize(proof, authorizer, invocation)
        if not result.allow or result.context is None or verifier is None:
            if verifier is not None:
                verifier.close()
            return None
        return result.context, verifier


class AuthorizedCardScheduleSource:
    """Project only owner-authorized CardStore records into Schedule input."""

    __slots__ = ("_backend", "_clock", "_store_factory", "_tenant_id")

    def __init__(self, backend, store_factory, tenant_id: str, *, clock=None) -> None:
        self._backend = backend
        self._store_factory = store_factory
        self._tenant_id = tenant_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def read(self, context, request, home):
        if type(context) is not SanitizedControlPlaneDecisionV1:
            raise PermissionError("authorized schedule source is unavailable")
        if type(request) is not ScheduleSourceRequest:
            raise PermissionError("authorized schedule source is unavailable")
        binding = context.binding
        acting = binding.agent_id or binding.principal.principal_id
        if (
            binding.target != SCHEDULE_TARGET
            or binding.capability != CAPABILITY
            or binding.resource_type != RESOURCE_TYPE
            or binding.resource_id is None
            or binding.owner_policy_revision is None
            or not context.joined_decision.allow
            or request.tenant_id != self._tenant_id
        ):
            raise PermissionError("authorized schedule source is unavailable")
        selection = AuthorizedCardPolicySelectionV1(
            subject=binding.principal.subject,
            acting_principal_id=acting,
            node_id=binding.node_id,
            resource_id=binding.resource_id,
            owner_policy_revision=binding.owner_policy_revision,
        )

        def project(entry):
            now = self._now()
            if (
                entry.subject != binding.principal.subject
                or entry.acting_principal_id != acting
                or entry.node_id != binding.node_id
                or entry.resource_id != binding.resource_id
                or entry.owner_policy_revision != binding.owner_policy_revision
                or not entry.valid_from <= now < entry.expires_at
                or not context.issued_at <= now < context.expires_at
                or entry.scope.role.replace("-", "_") != request.role
                or entry.scope.scope != request.scope
                or entry.scope.service != request.service_id
                or entry.scope.window != "latest"
                or entry.scope.baseline != "none"
            ):
                raise PermissionError("authorized schedule source is unavailable")
            store = self._store_factory(Path(home))
            before = self._fold(store, entry.visible_card_ids)
            before_facts = [self._card_facts(card) for card in before]
            after = self._fold(store, entry.visible_card_ids)
            if before_facts != [self._card_facts(card) for card in after]:
                raise PermissionError("authorized schedule source changed during read")
            visible_ids = frozenset(entry.visible_card_ids)
            field_mask = frozenset(entry.field_mask)
            items = [
                self._item(card, request, entry.owner_policy_revision)
                for card in after
                if not card.archived
            ]
            included_ids = frozenset(item["record_id"] for item in items)
            dependencies = (
                self._dependencies(after, included_ids, request)
                if "visible_edges" in field_mask
                else []
            )
            facts = {
                "owner_policy_revision": entry.owner_policy_revision,
                "visible_card_ids": sorted(visible_ids),
                "items": items,
                "dependencies": dependencies,
            }
            revision = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            )
            timestamp = now.isoformat().replace("+00:00", "Z")
            return {
                "schema_version": "1.0.0",
                "tenant_id": request.tenant_id,
                "snapshot_revision": revision,
                "observed_at": timestamp,
                "projected_at": timestamp,
                "authorization": {
                    "state": "authorized",
                    "target": SCHEDULE_TARGET,
                    "tenant_id": request.tenant_id,
                    "role": request.role,
                    "scope": request.scope,
                    "policy_decision_ref": entry.owner_policy_revision,
                    "owner_policy_revision": entry.owner_policy_revision,
                },
                "source_watermarks": [
                    {
                        "source": "skcoord.owner_policy",
                        "value": entry.owner_policy_revision,
                    },
                    {"source": "skcoord.card_store", "value": revision},
                ],
                "items": items,
                "dependencies": dependencies,
                "overlays": [],
            }

        result = self._backend.read_if_current(
            selection,
            binding.owner_policy_revision,
            project,
        )
        if not isinstance(result, dict):
            raise PermissionError("authorized schedule source is unavailable")
        return result

    @staticmethod
    def _fold(store, card_ids):
        cards = [store.fold(card_id) for card_id in card_ids]
        if any(card is None or card.id != card_id for card_id, card in zip(card_ids, cards)):
            raise PermissionError("authorized schedule source is unavailable")
        return cards

    @staticmethod
    def _card_facts(card):
        return {
            "id": card.id,
            "kind": card.kind.value,
            "title": card.title,
            "status": card.status.value,
            "labels": list(card.labels),
            "dependencies": list(card.dependencies),
            "archived": bool(card.archived),
            "created_at": card.created_at,
            "updated_at": card.updated_at,
        }

    @staticmethod
    def _semantic_type(card) -> str:
        labels = set(card.labels)
        if "milestone" in labels:
            return "milestone"
        if "project" in labels:
            return "project"
        if card.kind.value == "epic":
            return "epic"
        return "work_package"

    @staticmethod
    def _service(card) -> str:
        services = sorted(
            label[5:]
            for label in card.labels
            if isinstance(label, str)
            and label.startswith("repo:")
            and 5 < len(label) <= 133
            and label[5:].isascii()
        )
        return services[0] if services else "skcoord"

    @staticmethod
    def _unknown_dates():
        return {
            field: {
                "state": "unknown",
                "instant": None,
                "reason": f"no canonical {field} is recorded",
            }
            for field in DATE_FIELDS
        }

    def _item(self, card, request, owner_policy_revision):
        service = self._service(card)
        item = {
            "tenant_id": request.tenant_id,
            "record_id": card.id,
            "display_title": card.title,
            "semantic_type": self._semantic_type(card),
            "owner_service_id": service,
            "service_id": service,
            "lifecycle_status": card.status.value,
            "truth_state": "current",
            "visibility": {
                "state": "visible",
                "authorization": "authorized",
                "policy_decision_ref": owner_policy_revision,
            },
            "dates": self._unknown_dates(),
            "explicit_progress": None,
        }
        card_revision = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        return {
            **item,
            "source_watermarks": [{"source": "skcoord.card_store", "value": card_revision}],
            "evidence_refs": [f"skcoord.card_store:{card.id}"],
        }

    @staticmethod
    def _dependencies(cards, included_ids, request):
        status_by_id = {card.id: card.status.value for card in cards}
        edges = []
        for card in cards:
            if card.id not in included_ids:
                continue
            for target in sorted(set(card.dependencies) & included_ids):
                digest = hashlib.sha256(f"{card.id}\0{target}".encode()).hexdigest()
                edges.append(
                    {
                        "tenant_id": request.tenant_id,
                        "dependency_id": f"dependency:sha256:{digest}",
                        "source_item_id": card.id,
                        "target_item_id": target,
                        "edge_type": "finish_to_start",
                        "direction": "known",
                        "lag_seconds": 0,
                        "truth_state": "current",
                        "visibility": {
                            "state": "visible",
                            "authorization": "authorized",
                        },
                        "blocker_state": (
                            "not_blocking" if status_by_id[target] == "done" else "blocking"
                        ),
                        "evidence_refs": [f"skcoord.card_store:{card.id}#dependency"],
                    }
                )
        return edges

    def _now(self):
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("schedule source clock must use UTC")
        return value.astimezone(timezone.utc)


class _LiveOwnerPolicyProvider:
    """Reuse the exact project owner policy for approved read projections."""

    __slots__ = ("_project_provider", "_project_resource_id")

    def __init__(
        self, project_provider: AuthorizedCardPolicyProvider, project_resource_id: str
    ) -> None:
        self._project_provider = project_provider
        self._project_resource_id = project_resource_id

    def decide(self, binding, capauth_decision):
        if binding.target == TARGET and binding.capability == CAPABILITY:
            return self._project_provider.decide(binding, capauth_decision)
        if (binding.capability, binding.target) not in AUTHENTICATED_BINDINGS:
            return None
        # CapAuth already approved the actual target. These copies are used only
        # to select the same exact resource owner entry. The returned decision is
        # still joined against the original binding.
        overview_binding = binding.model_copy(
            update={
                "capability": CAPABILITY,
                "target": TARGET,
                "resource_type": RESOURCE_TYPE,
                "resource_id": self._project_resource_id,
            }
        )
        overview_decision = capauth_decision.model_copy(
            update={"scope": overview_binding.capability_scope()}
        )
        decision = self._project_provider.decide(overview_binding, overview_decision)
        if decision is None:
            return None
        return decision.model_copy(
            update={
                "resource_type": binding.resource_type,
                "resource_id": binding.resource_id,
            }
        )


def compose_file_backed_live_control_plane(
    *,
    config: LiveControlPlaneConfig,
    capability_authorizer,
    owner_policy_file: Path,
    store_factory,
    owner_policy_document: AuthorizedCardPolicyDocumentV1 | None = None,
    expected_policy_uid: int | None = None,
    credential_signer=None,
    operator_sessions: OperatorSessionManager | None = None,
    operator_revisions: CurrentPolicyRevisions | None = None,
    owner_policy_entries=None,
    clock=None,
) -> LiveControlPlaneComposition:
    """Compose the live runtime with SKCoord's durable fail-closed backend."""

    if owner_policy_document is not None:
        if not isinstance(owner_policy_document, AuthorizedCardPolicyDocumentV1):
            raise TypeError("verified owner policy document is required")
        backend = StaticAuthorizedCardPolicyBackend(owner_policy_document.entries)
    else:
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
        owner_policy_entries=owner_policy_entries,
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
    owner_policy_entries=None,
    clock=None,
) -> LiveControlPlaneComposition:
    """Compose CapAuth and SKCoord around one durable owner-provider instance."""

    provider_options = {"store_factory": store_factory}
    if clock is not None:
        provider_options["clock"] = clock
    provider = AuthorizedCardPolicyProvider(owner_policy_backend, **provider_options)
    live_owner_policy = _LiveOwnerPolicyProvider(provider, config.resource_id)
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
        AuthorizedCardScheduleSource(
            owner_policy_backend,
            store_factory,
            config.tenant_id,
            clock=clock,
        ),
        **schedule_options,
    )

    def invocation_factory(request, capability: str, target: str) -> ControlPlaneInvocationV1:
        if (capability, target) not in AUTHENTICATED_BINDINGS or request.url.path != target:
            raise PermissionError("control-plane invocation is outside the exact binding")
        origin = _approved_request_origin(request)
        return ControlPlaneInvocationV1(
            node_id=config.node_id,
            purpose=PURPOSE,
            audience=AUDIENCE,
            capability=capability,
            target=target,
            resource_type=(
                EVENTS_RESOURCE_TYPE if capability == EVENTS_CAPABILITY else RESOURCE_TYPE
            ),
            resource_id=config.tenant_id
            if capability == EVENTS_CAPABILITY
            else config.resource_id,
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
            owner_policy_entries=owner_policy_entries,
            clock=clock,
        )

    return LiveControlPlaneComposition(
        decision_authorizer=authorizer,
        invocation_factory=invocation_factory,
        project_provider=provider,
        schedule_provider=schedule_provider,
        reliability_provider=ReliabilityProjectionProvider(),
        session_authorizer=session_authorizer,
        legacy_board_url=config.legacy_board_url,
    )


__all__ = [
    "AUDIENCE",
    "AUTHENTICATED_BINDINGS",
    "AUTHENTICATED_TARGETS",
    "BOARD_TARGET",
    "CAPABILITY",
    "EVENTS_CAPABILITY",
    "FLEET_CHAT_TARGET",
    "EVENTS_TARGET",
    "AuthorizedCardScheduleSource",
    "LiveControlPlaneComposition",
    "LiveControlPlaneConfig",
    "InProcessOperatorBridge",
    "NODE_ID",
    "PURPOSE",
    "RESOURCE_TYPE",
    "RELIABILITY_TARGET",
    "SCHEDULE_TARGET",
    "TARGET",
    "compose_file_backed_live_control_plane",
    "compose_live_control_plane",
]
