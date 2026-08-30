import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from skcoord.authorized_card_policy import (
    AuthorizedCardPolicyDocumentV1,
    AuthorizedCardPolicyEntryV1,
    AuthorizedCardPolicySelectionV1,
    AuthorizedCardScopeV1,
)
from starlette.requests import Request
from starlette.testclient import TestClient

from skdashboard.control_plane_api import ALLOWED_BROWSER_ORIGINS
from skdashboard.dashboard_schedule import (
    AUTHORIZATION_TARGET,
    ScheduleProjectionProvider,
)
from skdashboard.live_control_plane import (
    CAPABILITY,
    RESOURCE_TYPE,
    SCHEDULE_TARGET,
    TARGET,
    LiveControlPlaneConfig,
    compose_file_backed_live_control_plane,
    compose_live_control_plane,
)
from skdashboard.read_only import _read_exact_value_free_config, create_read_only_app

ORIGIN = sorted(ALLOWED_BROWSER_ORIGINS)[0]
RESOURCE_ID = "authorized-card-set:sha256:" + "a" * 64
POLICY_REVISION = "b" * 64


def config(tmp_path: Path, *, board="https://legacy.example/board") -> LiveControlPlaneConfig:
    return LiveControlPlaneConfig(
        legacy_board_url=board,
        resource_id=RESOURCE_ID,
        owner_policy_revision=POLICY_REVISION,
        tenant_id="platform",
        capability_ttl_seconds=60,
    )


def request(
    *, path=TARGET, origin=ORIGIN, request_id="request-1", host="10.0.0.139:7778"
) -> Request:
    headers = [(b"host", host.encode()), (b"x-request-id", request_id.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("10.0.0.139", 7778),
            "client": ("127.0.0.1", 1),
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
        }
    )


def test_composition_uses_one_provider_for_owner_decision_and_read(tmp_path) -> None:
    backend = Mock()
    capability_authorizer = Mock()
    composition = compose_live_control_plane(
        config=config(tmp_path),
        capability_authorizer=capability_authorizer,
        owner_policy_backend=backend,
        store_factory=Mock(),
    )

    assert (
        composition.decision_authorizer._owner_policy._project_provider
        is composition.project_provider
    )
    invocation = composition.invocation_factory(request(), CAPABILITY, TARGET)
    assert invocation.node_id == "chiap08"
    assert invocation.purpose == "project-management-reporting"
    assert invocation.audience == "skdashboard"
    assert invocation.capability == "skdashboard.read"
    assert invocation.target == "/api/v1/overview"
    assert invocation.resource_type == RESOURCE_TYPE
    assert invocation.resource_id == RESOURCE_ID
    assert invocation.boundary.origin == ORIGIN
    assert isinstance(composition.schedule_provider, ScheduleProjectionProvider)


def test_invocation_factory_derives_exact_same_origin_when_browser_omits_origin(
    tmp_path,
) -> None:
    composition = compose_live_control_plane(
        config=config(tmp_path),
        capability_authorizer=Mock(),
        owner_policy_backend=Mock(),
        store_factory=Mock(),
    )

    invocation = composition.invocation_factory(request(origin=None), CAPABILITY, TARGET)

    assert invocation.boundary.origin == "https://10.0.0.139:7778"


def test_invocation_factory_rejects_unapproved_derived_origin(tmp_path) -> None:
    composition = compose_live_control_plane(
        config=config(tmp_path),
        capability_authorizer=Mock(),
        owner_policy_backend=Mock(),
        store_factory=Mock(),
    )

    with pytest.raises(PermissionError, match="origin is not approved"):
        composition.invocation_factory(
            request(origin=None, host="untrusted.example"), CAPABILITY, TARGET
        )


def test_verified_owner_policy_bytes_survive_post_verification_path_swap(
    tmp_path: Path, monkeypatch
) -> None:
    current = datetime.now(timezone.utc)
    first = AuthorizedCardPolicyEntryV1.issue(
        subject="jarvis",
        acting_principal_id="jarvis",
        node_id="chiap08",
        scope=AuthorizedCardScopeV1(role="project-manager"),
        valid_from=current - timedelta(minutes=1),
        expires_at=current + timedelta(hours=1),
        visible_card_ids=("alpha",),
    )
    second = AuthorizedCardPolicyEntryV1.issue(
        subject="jarvis",
        acting_principal_id="jarvis",
        node_id="chiap08",
        scope=AuthorizedCardScopeV1(role="project-manager"),
        valid_from=current - timedelta(minutes=1),
        expires_at=current + timedelta(hours=1),
        visible_card_ids=("beta",),
    )
    first_document = AuthorizedCardPolicyDocumentV1(entries=(first,))
    policy = tmp_path / "owner-policy.json"
    policy.write_text(first_document.model_dump_json(), encoding="utf-8")
    policy.chmod(0o600)
    verified = AuthorizedCardPolicyDocumentV1.model_validate_json(
        _read_exact_value_free_config(
            policy,
            hashlib.sha256(policy.read_bytes()).hexdigest(),
            expected_uid=policy.stat().st_uid,
        )
    )
    policy.write_text(
        AuthorizedCardPolicyDocumentV1(entries=(second,)).model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "skdashboard.live_control_plane.compose_live_control_plane",
        lambda **values: values["owner_policy_backend"],
    )

    backend = compose_file_backed_live_control_plane(
        config=LiveControlPlaneConfig(
            legacy_board_url="https://legacy.example/board",
            resource_id=first.resource_id,
            owner_policy_revision=first.owner_policy_revision,
            tenant_id="platform",
        ),
        capability_authorizer=Mock(),
        owner_policy_file=policy,
        owner_policy_document=verified,
        store_factory=Mock(),
    )
    first_selection = AuthorizedCardPolicySelectionV1(
        subject=first.subject,
        acting_principal_id=first.acting_principal_id,
        node_id=first.node_id,
        resource_id=first.resource_id,
        owner_policy_revision=first.owner_policy_revision,
    )
    second_selection = AuthorizedCardPolicySelectionV1(
        subject=second.subject,
        acting_principal_id=second.acting_principal_id,
        node_id=second.node_id,
        resource_id=second.resource_id,
        owner_policy_revision=second.owner_policy_revision,
    )

    assert backend.snapshot(first_selection) == first
    assert backend.snapshot(second_selection) is None


def test_composed_schedule_provider_is_honestly_unavailable(tmp_path) -> None:
    composition = compose_live_control_plane(
        config=config(tmp_path),
        capability_authorizer=Mock(),
        owner_policy_backend=Mock(),
        store_factory=Mock(),
    )
    context = SimpleNamespace(
        binding=SimpleNamespace(target=AUTHORIZATION_TARGET, capability=CAPABILITY),
        joined_decision=SimpleNamespace(allow=True),
    )
    verifier = SimpleNamespace(
        check_before_owner_read=lambda _context: SimpleNamespace(value="allow"),
        check_after_owner_read=lambda _context: SimpleNamespace(value="allow"),
    )
    query = {
        "role": "project-manager",
        "scope": "estate",
        "window": "latest",
        "baseline": "none",
        "service": "all",
        "lens": "roadmap",
        "timezone": "UTC",
    }

    with pytest.raises(PermissionError, match="authorized schedule projection unavailable"):
        composition.schedule_provider.read(
            context,
            query,
            tmp_path,
            currentness_verifier=verifier,
        )


@pytest.mark.parametrize(
    ("capability", "target", "origin"),
    [
        ("skdashboard.events.read", TARGET, ORIGIN),
        (CAPABILITY, "/api/v1/board/summary", ORIGIN),
        (CAPABILITY, TARGET, "https://untrusted.example"),
    ],
)
def test_invocation_factory_rejects_every_nonexact_binding(
    tmp_path, capability, target, origin
) -> None:
    composition = compose_live_control_plane(
        config=config(tmp_path),
        capability_authorizer=Mock(),
        owner_policy_backend=Mock(),
        store_factory=Mock(),
    )
    with pytest.raises(PermissionError):
        composition.invocation_factory(request(path=target, origin=origin), capability, target)


def test_live_composition_serves_real_projects_and_schedule(tmp_path, monkeypatch) -> None:
    from capauth import (
        CurrentPolicyRevisions,
        InMemoryOperatorSessionBackendForTests,
        OperatorSessionManager,
    )
    from capauth.delegated import (
        CapabilityAuthorizer,
        InMemoryAuditSink,
        InMemoryPrincipalPolicyBackend,
        InMemoryReplayBackend,
        InMemoryRevocationBackend,
        IssuerGrant,
        Principal,
        StaticTrustedIssuerBackend,
    )
    from skcoord.authorized_card_policy import (
        AuthorizedCardPolicyEntryV1,
        StaticAuthorizedCardPolicyBackend,
    )
    from skcoord.authorized_card_snapshot import AuthorizedCardScopeV1
    from skcoord.card import Card, Column, Kind
    from test_control_plane_decision_context import Signer

    from skdashboard import control_plane_adapters, control_plane_quality

    now = datetime.now(timezone.utc)
    principal = Principal(
        principal_id="human@example.test",
        subject="human@example.test",
        kind="human",
    )
    scope = AuthorizedCardScopeV1(role="project-manager")
    entry = AuthorizedCardPolicyEntryV1.issue(
        subject=principal.subject,
        acting_principal_id=principal.principal_id,
        node_id="chiap08",
        scope=scope,
        valid_from=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=2),
        visible_card_ids=("source",),
        field_mask=("owner_ref", "visible_edges"),
        semantic_classes=("project",),
    )
    source = Card(
        id="source",
        kind=Kind.TASK,
        title="Authorized schedule record",
        description="protected description must never be projected",
        status=Column.DOING,
        swimlane="feature",
        priority="high",
        originator="fixture",
        owner="project-owner",
        labels=["project"],
        acceptance_criteria=[],
        dependencies=["hidden-card"],
        links={},
        meta={},
        created_at="2026-08-20T00:00:00Z",
        updated_at="2026-08-29T20:00:00Z",
    )

    store_reads = []

    class Store:
        def fold(self, card_id):
            store_reads.append(card_id)
            return source if card_id == source.id else None

    signer = Signer()

    def clock():
        return now

    capability_authorizer = CapabilityAuthorizer(
        trusted_issuers=StaticTrustedIssuerBackend(
            (
                IssuerGrant(
                    fingerprint=signer.issuer_fingerprint,
                    capabilities=frozenset({CAPABILITY}),
                    audiences=frozenset({"skdashboard"}),
                    principal_kinds=frozenset({"human"}),
                ),
            )
        ),
        principals=InMemoryPrincipalPolicyBackend((principal,)),
        revocations=InMemoryRevocationBackend(),
        replay=InMemoryReplayBackend(clock=clock),
        audit=InMemoryAuditSink(),
        signature_verifier=signer,
        clock=clock,
    )
    cfg = LiveControlPlaneConfig(
        legacy_board_url="https://legacy.example/board",
        resource_id=entry.resource_id,
        owner_policy_revision=entry.owner_policy_revision,
        tenant_id="platform",
        capability_ttl_seconds=60,
    )
    composition = compose_live_control_plane(
        config=cfg,
        capability_authorizer=capability_authorizer,
        owner_policy_backend=StaticAuthorizedCardPolicyBackend((entry,)),
        store_factory=lambda _home: Store(),
        credential_signer=signer,
        operator_sessions=OperatorSessionManager(
            backend=InMemoryOperatorSessionBackendForTests(),
            current_revisions=lambda _binding: revisions,
            enabled=True,
            clock=clock,
        ),
        operator_revisions=(
            revisions := CurrentPolicyRevisions(
                issuer="1" * 64,
                principal="2" * 64,
                acting_principal="3" * 64,
                revocation="4" * 64,
                owner=entry.owner_policy_revision,
            )
        ),
        clock=clock,
    )
    bridge = composition.session_authorizer
    session_record = bridge.enroll(principal.subject, ORIGIN)
    assert isinstance(session_record, str)
    assert session_record.isascii()
    assert 32 <= len(session_record) <= 128

    direct_request = request(path=SCHEDULE_TARGET, request_id="schedule-direct-read")
    direct_authority = bridge(
        direct_request,
        SimpleNamespace(control_plane_request=bridge.request(session_record, direct_request)),
        CAPABILITY,
        SCHEDULE_TARGET,
        composition.decision_authorizer,
        composition.invocation_factory,
    )
    direct_context, direct_verifier = direct_authority
    direct_projection = composition.schedule_provider.read(
        direct_context,
        {
            "role": "project-manager",
            "scope": "estate",
            "window": "latest",
            "baseline": "none",
            "service": "all",
            "lens": "roadmap",
            "timezone": "UTC",
        },
        tmp_path,
        currentness_verifier=direct_verifier,
    )
    assert direct_projection["items"][0]["item_id"] == source.id
    direct_watermarks = direct_projection["items"][0]["source_watermarks"]
    source.dependencies[:] = ["different-hidden-card"]

    async def resolve_session(incoming_request):
        return SimpleNamespace(
            state="authenticated",
            subject=principal.subject,
            control_plane_request=bridge.request(session_record, incoming_request),
        )

    monkeypatch.setattr(control_plane_adapters, "default_readers", lambda _home: {})
    monkeypatch.setattr(control_plane_adapters, "project_estate", lambda _readers: [])
    monkeypatch.setattr(
        control_plane_quality,
        "project_data_quality",
        lambda _items: {"projection_type": "data_quality", "truth_state": "current"},
    )
    session = SimpleNamespace(resolve=resolve_session, routes=lambda: [])
    app = create_read_only_app(
        tmp_path,
        decision_authorizer=composition.decision_authorizer,
        invocation_factory=composition.invocation_factory,
        project_provider=composition.project_provider,
        schedule_provider=composition.schedule_provider,
        session_adapter=session,
        session_authorizer=composition.session_authorizer,
        legacy_board_url=composition.legacy_board_url,
    )
    client = TestClient(app, base_url=ORIGIN)
    headers = {"Origin": ORIGIN}
    overview_query = "?role=project-manager&scope=estate&window=latest&baseline=none&service=all"
    schedule_query = overview_query + "&lens=roadmap&timezone=UTC"

    assert client.get("/control-plane/now").status_code == 200
    assert client.get("/control-plane/portfolio").status_code == 200
    overview = client.get("/api/v1/overview" + overview_query, headers=headers)
    schedule = client.get(SCHEDULE_TARGET + schedule_query, headers=headers)

    assert overview.status_code == 200, overview.text
    project = next(
        item
        for item in overview.json()["items"]
        if item.get("projection_type") == "project_records"
    )
    assert project["truth_state"] == "current"
    assert project["records"][0]["record_id"] == source.id
    assert schedule.status_code == 200, schedule.text
    schedule_projection = schedule.json()
    assert schedule_projection["items"][0]["item_id"] == source.id
    assert schedule_projection["items"][0]["title"] == source.title
    assert schedule_projection["items"][0]["source_watermarks"] == direct_watermarks
    assert schedule_projection["dependencies"] == []
    assert "protected description" not in schedule.text
    assert store_reads == [source.id] * 5
    assert schedule_projection["items"][0]["dates"]["planned_target"] == {
        "state": "unknown",
        "instant": None,
        "reason": "no canonical planned_target is recorded",
    }


def test_file_backed_composition_constructs_the_durable_owner_backend(
    tmp_path, monkeypatch
) -> None:
    created = {}

    class Backend:
        def __init__(self, path, **options):
            created.update(path=path, options=options)

    monkeypatch.setattr("skdashboard.live_control_plane.FileAuthorizedCardPolicyBackend", Backend)
    composition = compose_file_backed_live_control_plane(
        config=config(tmp_path),
        capability_authorizer=Mock(),
        owner_policy_file=tmp_path / "owner-policy.json",
        expected_policy_uid=1234,
        store_factory=Mock(),
    )

    assert created == {
        "path": tmp_path / "owner-policy.json",
        "options": {"expected_uid": 1234},
    }
    assert composition.project_provider._backend.__class__ is Backend
    assert (
        composition.decision_authorizer._owner_policy._project_provider
        is composition.project_provider
    )


def test_read_only_runtime_serves_now_portfolio_schedule_static_and_external_board(
    tmp_path,
) -> None:
    board = "https://legacy.example/explicit-board"
    legacy_origin = "https://legacy.example"
    legacy_paths = (
        "/cockpit",
        "/cmdb",
        "/board",
        "/assistant",
        "/trust",
        "/models",
        "/economy",
        "/fleet",
    )
    app = create_read_only_app(tmp_path, legacy_board_url=board)
    client = TestClient(app, base_url=ORIGIN)

    pages = {
        name: client.get(f"/control-plane/{name}")
        for name in (
            "now",
            "portfolio",
            "schedule",
            "reliability",
            "architecture",
            "ai",
            "governance",
            "reports",
        )
    }
    css = client.get("/static/css/overview.css")
    javascript = client.get("/static/js/overview.js")
    assert all(response.status_code == 200 for response in pages.values())
    assert css.status_code == 200
    assert "<h2>Now</h2>" in pages["now"].text
    assert "Portfolio" in pages["portfolio"].text
    assert "Schedule" in pages["schedule"].text
    for response in pages.values():
        for path in legacy_paths:
            expected = board if path == "/board" else f"{legacy_origin}{path}"
            assert f'href="{expected}"' in response.text
            assert f'href="{path}"' not in response.text
    for asset in ("overview.js", "projects.js"):
        rewritten = client.get(f"/static/js/{asset}")
        for path in legacy_paths:
            assert f'href="{path}"' not in rewritten.text
    assert board in javascript.text
    assert f'href="{legacy_origin}/cockpit"' in javascript.text
    assert f'href="{legacy_origin}/cmdb"' in javascript.text
    for asset in ("overview.js", "projects.js", "schedule.js"):
        script = client.get(f"/static/js/{asset}")
        assert script.status_code == 200
        assert 'from "./api.js"' not in script.text
        assert 'from "./read_only_api.js"' in script.text
    assert "editor.js" not in javascript.text
    helper = client.get("/static/js/read_only_api.js")
    assert helper.status_code == 200
    assert "/api/auth/capability" not in helper.text
    assert "localStorage" not in helper.text
    assert client.get("/static/js/api.js").status_code == 404
    assert client.get("/static/js/editor.js").status_code == 404
    assert client.get("/board").status_code == 404
    assert client.post("/api/card/example/mutate").status_code == 404
    route_paths = {route.path for route in app.routes}
    assert {
        "/api/v1/overview",
        "/api/v1/schedule/projection",
        "/api/v1/schedule/forecasts",
        "/api/v1/board/summary",
        "/api/v1/fleet/summary",
        "/api/v1/economy/summary",
        "/api/v1/events",
    } <= route_paths


def test_legacy_board_configuration_rejects_urls_with_credentials_or_query(
    tmp_path,
) -> None:
    for value in (
        "http://legacy.example/board",
        "https://user:secret@legacy.example/board",
        "https://legacy.example/board?token=secret",
    ):
        with pytest.raises(ValueError):
            config(tmp_path, board=value)
