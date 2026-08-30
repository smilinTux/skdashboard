from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
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
from skdashboard.read_only import create_read_only_app

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


def request(*, path=TARGET, origin=ORIGIN, request_id="request-1") -> Request:
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
            "headers": [
                (b"host", b"10.0.0.139:7778"),
                (b"origin", origin.encode()),
                (b"x-request-id", request_id.encode()),
            ],
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


def test_live_composition_serves_real_projects_and_reaches_unavailable_schedule(
    tmp_path, monkeypatch
) -> None:
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
        title="not projected",
        description="not projected",
        status=Column.DOING,
        swimlane="feature",
        priority="high",
        originator="fixture",
        owner="project-owner",
        labels=["project"],
        acceptance_criteria=[],
        dependencies=[],
        links={},
        meta={},
        created_at="2026-08-20T00:00:00Z",
        updated_at="2026-08-29T20:00:00Z",
    )

    class Store:
        def fold(self, card_id):
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
    assert schedule.status_code == 503
    assert schedule.json()["code"] == "SCHEDULE_UNAVAILABLE"


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
    app = create_read_only_app(tmp_path, legacy_board_url=board)
    client = TestClient(app, base_url=ORIGIN)

    now = client.get("/control-plane/now")
    portfolio = client.get("/control-plane/portfolio")
    schedule = client.get("/control-plane/schedule")
    css = client.get("/static/css/overview.css")
    javascript = client.get("/static/js/overview.js")
    assert (
        now.status_code == portfolio.status_code == schedule.status_code == css.status_code == 200
    )
    assert "<h2>Now</h2>" in now.text
    assert "Portfolio" in portfolio.text
    assert "Schedule" in schedule.text
    assert f'href="{board}"' in now.text
    assert f'href="{board}"' in portfolio.text
    assert f'href="{board}"' in schedule.text
    assert 'href="/board"' not in now.text + portfolio.text + schedule.text
    assert board in javascript.text
    assert 'href="/board"' not in javascript.text
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
