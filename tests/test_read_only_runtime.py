import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from skdashboard.read_only import (
    ALLOWED_BIND_HOSTS,
    ALLOWED_REQUEST_HOSTS,
    HSTS_POLICY,
    CallbackAccessLogFilter,
    create_read_only_app,
    main,
)

LAN_ORIGIN = "https://10.0.0.139:7778"
TAILNET_ORIGIN = "https://100.81.238.58:7778"
TAILNET_FQDN_ORIGIN = "https://chiap08.tail204f0c.ts.net:7778"

# Test fingerprints for different operators
CASEY_FINGERPRINT = "AD80D077A047BABF29EEC97AF454FDBC3B1C37D9"
JARVIS_FINGERPRINT = "C8D406A46F2DF4894E4FB41580A638570C9D41C4"


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_read_only_app(tmp_path, authorizer=lambda *_: True), base_url=LAN_ORIGIN
    )


def test_route_inventory_is_read_only(tmp_path: Path) -> None:
    app = create_read_only_app(tmp_path, authorizer=lambda *_: True)
    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", ()) or ())))
        for route in app.routes
    }
    assert not any("POST" in methods for _, methods in routes)
    assert {"127.0.0.1", "10.0.0.139", "100.81.238.58"} == ALLOWED_BIND_HOSTS


def test_approved_surfaces_exist_and_legacy_privilege_is_absent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/").status_code == 200
    workspaces = {
        "now": "overview",
        "portfolio": "projects",
        "schedule": "schedule",
        "reliability": "reliability",
        "architecture": "architecture",
        "ai": "ai",
        "governance": "governance",
        "reports": "reports",
    }
    for route, asset in workspaces.items():
        assert client.get(f"/control-plane/{route}").status_code == 200
        script = client.get(f"/static/js/{asset}.js")
        assert script.status_code == 200
        assert 'from "./api.js"' not in script.text
        assert 'from "./read_only_api.js"' in script.text
        assert client.get(f"/static/css/{asset}.css").status_code == 200
    assert client.get("/.well-known/skworld-module.json").status_code == 200
    manifest = client.get("/.well-known/skworld-module.json").json()
    assert manifest["health"].endswith("/api/v1/health")
    assert manifest["auth"] == {
        "audience": "skdashboard",
        "scopes": ["skdashboard.read"],
    }
    assert "operator" not in manifest
    assert client.get("/api/v1/health").status_code == 200
    headers = {"Authorization": "Bearer test", "Origin": LAN_ORIGIN}
    assert client.get("/api/v1/overview", headers=headers).status_code == 200
    assert client.get("/metrics", headers=headers).status_code == 200
    assert client.get("/fleet-chat").status_code == 200
    assert client.get("/static/js/fleet_chat.js").status_code == 200
    assert client.get("/api/v1/fleet-chat").status_code == 401
    chat = client.get("/api/v1/fleet-chat", headers=headers)
    assert chat.status_code == 200
    assert chat.json()["source"] == "skmail"
    assert chat.json()["read_only"] is True
    for path in (
        "/api/auth/capability",
        "/api/card/x/mutate",
        "/api/card/x/queue-ai",
        "/api/assistant",
        "/api/cmdb/apply",
        "/api/cmdb/seed",
        "/api/models/advertise",
        "/static/assistant.html",
        "/static/cmdb.html",
        "/static/models.html",
        "/static/js/api.js",
        "/static/js/assistant.js",
        "/static/js/cmdb.js",
        "/static/js/ai_compose.js",
    ):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404


def test_exact_https_origins_redirect_hsts_and_public_host_denial(
    tmp_path: Path,
) -> None:
    app = create_read_only_app(tmp_path, authorizer=lambda *_: True)
    assert ALLOWED_REQUEST_HOSTS == {"10.0.0.139", "chiap08.tail204f0c.ts.net"}
    for origin in (LAN_ORIGIN, TAILNET_FQDN_ORIGIN):
        response = TestClient(app, base_url=origin).get("/api/v1/health")
        assert response.status_code == 200
        assert response.headers["strict-transport-security"] == HSTS_POLICY

    assert TestClient(app, base_url=TAILNET_ORIGIN).get("/").status_code == 400

    redirect = TestClient(app, base_url="http://10.0.0.139:7778").get(
        "/api/v1/health?probe=1", follow_redirects=False
    )
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "https://10.0.0.139:7778/api/v1/health?probe=1"
    assert TestClient(app, base_url="https://public.example").get("/").status_code == 400


def test_cookie_transport_is_secure_host_only_and_session_routes_stay_disabled(
    tmp_path: Path,
) -> None:
    app = create_read_only_app(tmp_path, authorizer=lambda *_: True)

    async def synthetic_cookie(_request):
        return Response(headers={"Set-Cookie": "sid=test; Domain=example.test; HttpOnly"})

    app.routes.append(Route("/__transport_test__", synthetic_cookie))
    response = TestClient(app, base_url=LAN_ORIGIN).get("/__transport_test__")
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "Domain=" not in cookie
    assert not any("session" in route.path for route in app.routes)


def test_launcher_requires_exact_tls_files_without_persistent_transport_state(
    tmp_path: Path, monkeypatch
) -> None:
    observed = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: observed.update(kwargs))

    main(
        [
            "--home",
            str(tmp_path),
            "--host",
            "10.0.0.139",
            "--tls-certfile",
            "/run/credentials/skdashboard.crt",
            "--tls-keyfile",
            "/run/credentials/skdashboard.key",
        ]
    )

    assert observed["host"] == "10.0.0.139"
    assert observed["ssl_certfile"] == "/run/credentials/skdashboard.crt"
    assert observed["ssl_keyfile"] == "/run/credentials/skdashboard.key"
    access_handler = observed["log_config"]["handlers"]["access"]
    assert "redact_oidc_callback" in access_handler["filters"]
    assert list(tmp_path.iterdir()) == []


def test_launcher_composes_authenticated_file_backed_runtime(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from skcoord.authorized_card_policy import (
        AuthorizedCardPolicyDocumentV1,
        AuthorizedCardPolicyEntryV1,
        AuthorizedCardScopeV1,
    )

    observed = {}
    monkeypatch.setattr(
        "skdashboard.live_control_plane.compose_file_backed_live_control_plane",
        lambda **values: (
            observed.update(composition=values)
            or SimpleNamespace(
                decision_authorizer="typed-authorizer",
                invocation_factory="invocation-factory",
                project_provider="durable-provider",
                schedule_provider="schedule-provider",
                reliability_provider="reliability-provider",
                session_authorizer="in-process-authorizer",
                legacy_board_url="https://legacy.example/board",
            )
        ),
    )
    monkeypatch.setattr(
        "skdashboard.session_adapter.EncryptedSessionAdapter",
        lambda *args, **kwargs: SimpleNamespace(resolve="session-resolver", routes=lambda: []),
    )
    monkeypatch.setattr(
        "skdashboard.runtime_authorizer.build",
        lambda **values: observed.update(authorizer=values) or "durable-authorizer",
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: observed.update(app=app))
    for name in ("session.key", "client.secret"):
        path = tmp_path / name
        path.write_text("non-secret-fixture", encoding="utf-8")
        path.chmod(0o600)
    current = datetime.now(timezone.utc)
    entry = AuthorizedCardPolicyEntryV1.issue(
        subject=JARVIS_FINGERPRINT,
        acting_principal_id=JARVIS_FINGERPRINT,
        node_id="chiap08",
        scope=AuthorizedCardScopeV1(role="project-manager"),
        valid_from=current - timedelta(minutes=5),
        expires_at=current + timedelta(hours=1),
    )
    owner_policy = tmp_path / "owner-policy.json"
    owner_policy.write_text(
        AuthorizedCardPolicyDocumentV1(entries=(entry,)).model_dump_json(),
        encoding="utf-8",
    )
    owner_policy.chmod(0o600)
    owner_policy_sha256 = hashlib.sha256(owner_policy.read_bytes()).hexdigest()
    revisions = tmp_path / "revisions.json"
    revisions.write_text(
        json.dumps(
            {
                "issuer": "1" * 64,
                "principal": "2" * 64,
                "acting_principal": "3" * 64,
                "revocation": "4" * 64,
                "owner": entry.owner_policy_revision,
            }
        ),
        encoding="utf-8",
    )
    revisions.chmod(0o600)
    gnupg = tmp_path / "gnupg"
    gnupg.mkdir(mode=0o700)
    signer_passphrase = tmp_path / "signer.passphrase"
    signer_passphrase.write_bytes(b"bounded test passphrase")
    signer_passphrase.chmod(0o600)
    trusted = tmp_path / "trusted-issuers.json"
    trusted.write_text('{"value_free":true}', encoding="ascii")
    trusted.chmod(0o600)
    trusted_sha256 = hashlib.sha256(trusted.read_bytes()).hexdigest()
    trusted_signature = tmp_path / "trusted-issuers.json.asc"
    trusted_signature.write_text("test signature", encoding="ascii")
    trusted_signature.chmod(0o600)
    trusted_signature_sha256 = hashlib.sha256(trusted_signature.read_bytes()).hexdigest()
    revisions.write_text(
        revisions.read_text(encoding="utf-8").replace("1" * 64, trusted_sha256),
        encoding="utf-8",
    )

    main(
        [
            "--home",
            str(tmp_path),
            "--host",
            "10.0.0.139",
            "--tls-certfile",
            "/run/credentials/skdashboard.crt",
            "--tls-keyfile",
            "/run/credentials/skdashboard.key",
            "--session-db",
            str(tmp_path / "sessions.db"),
            "--session-key-file",
            str(tmp_path / "session.key"),
            "--oidc-issuer",
            "https://issuer.example",
            "--oidc-redirect-uri",
            f"{LAN_ORIGIN}/auth/callback",
            "--oidc-client-secret-file",
            str(tmp_path / "client.secret"),
            "--legacy-board-url",
            "https://legacy.example/board",
            "--authorized-resource-id",
            entry.resource_id,
            "--owner-policy-file",
            str(owner_policy),
            "--owner-policy-sha256",
            owner_policy_sha256,
            "--owner-policy-revision",
            entry.owner_policy_revision,
            "--tenant-id",
            "platform",
            "--operator-session-db",
            str(tmp_path / "operator-sessions.db"),
            "--operator-policy-revisions-file",
            str(revisions),
            "--operator-policy-revisions-sha256",
            hashlib.sha256(revisions.read_bytes()).hexdigest(),
            "--signer-fingerprint",
            "DCE38ED7BC9D95D724B5FE7FECF9D6A423EC83F5",
            "--signer-gnupg-home",
            str(gnupg),
            "--signer-passphrase-file",
            str(signer_passphrase),
            "--trusted-issuer-policy-file",
            str(trusted),
            "--trusted-issuer-policy-sha256",
            trusted_sha256,
            "--trusted-issuer-signature-file",
            str(trusted_signature),
            "--trusted-issuer-signature-sha256",
            trusted_signature_sha256,
            "--capability-state-db",
            str(tmp_path / "capability-state.db"),
            "--capability-authorizer-factory",
            "skdashboard.runtime_authorizer:build",
        ]
    )

    assert observed["composition"]["owner_policy_file"] == owner_policy
    assert observed["composition"]["owner_policy_document"] == AuthorizedCardPolicyDocumentV1(
        entries=(entry,)
    )
    assert observed["composition"]["capability_authorizer"] == "durable-authorizer"
    assert observed["authorizer"]["trusted_issuer_policy_file"] == trusted
    assert observed["authorizer"]["principals"][0].principal_id == entry.acting_principal_id
    routes = {route.path for route in observed["app"].routes}
    assert {
        "/control-plane/now",
        "/control-plane/portfolio",
        "/control-plane/schedule",
        "/matters",
        "/tasks",
        "/work-queue",
        "/api/v1/overview",
        "/api/v1/schedule/projection",
        "/api/v1/schedule/forecasts",
        "/api/v1/board/summary",
        "/api/v1/fleet/summary",
        "/api/v1/economy/summary",
        "/api/v1/gateway/summary",
        "/api/v1/events",
    } <= routes
    assert observed["app"].routes


def test_workspace_routes_are_truthful_read_only_entrypoints(tmp_path: Path) -> None:
    app = create_read_only_app(
        tmp_path,
        authorizer=lambda *_: True,
        legacy_board_url="https://legacy.example/board",
    )
    client = TestClient(app, base_url=LAN_ORIGIN)

    matters = client.get("/matters", follow_redirects=False)
    tasks = client.get("/tasks", follow_redirects=False)
    queue = client.get("/work-queue", follow_redirects=False)

    assert matters.status_code == tasks.status_code == queue.status_code == 307
    assert matters.headers["location"].endswith("selected_silo=legal")
    assert tasks.headers["location"] == "https://legacy.example/board"
    assert queue.headers["location"] == "https://legacy.example/board"
    assert all(
        response.headers["cache-control"] == "no-store" for response in (matters, tasks, queue)
    )


def test_queue_routes_fail_closed_without_authorized_board_source(tmp_path: Path) -> None:
    client = TestClient(create_read_only_app(tmp_path), base_url=LAN_ORIGIN)

    for path in ("/tasks", "/work-queue"):
        response = client.get(path)
        assert response.status_code == 503
        assert "No authorized legacy board source is configured" in response.text


def test_runtime_authorizer_and_config_drift_fail_closed(tmp_path: Path) -> None:
    from skdashboard.read_only import _read_exact_value_free_config

    config = tmp_path / "policy.json"
    config.write_text("{}", encoding="utf-8")
    config.chmod(0o600)
    exact = hashlib.sha256(config.read_bytes()).hexdigest()

    assert _read_exact_value_free_config(config, exact, expected_uid=config.stat().st_uid) == b"{}"
    config.chmod(0o620)
    with pytest.raises(ValueError, match="configuration file is unsafe"):
        _read_exact_value_free_config(config, exact, expected_uid=config.stat().st_uid)
    config.chmod(0o600)
    config.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        _read_exact_value_free_config(config, exact, expected_uid=config.stat().st_uid)


def test_unavailable_schedule_sources_report_degraded_state(tmp_path: Path) -> None:
    app = create_read_only_app(tmp_path, authorizer=lambda *_: True)
    client = TestClient(app, base_url=LAN_ORIGIN)
    headers = {"Authorization": "Bearer test", "Origin": LAN_ORIGIN}
    query = (
        "?role=project-manager&scope=estate&window=latest&baseline=none"
        "&service=all&lens=roadmap&timezone=UTC"
    )

    projection = client.get("/api/v1/schedule/projection" + query, headers=headers)
    forecast = client.get("/api/v1/schedule/forecasts" + query, headers=headers)
    assert projection.status_code == forecast.status_code == 503
    assert projection.json()["code"] == "SCHEDULE_UNAVAILABLE"
    assert forecast.json()["code"] == "SCHEDULE_FORECAST_UNAVAILABLE"


def test_launcher_rejects_partial_live_composition(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(SystemExit):
        main(
            [
                "--host",
                "10.0.0.139",
                "--tls-certfile",
                "/run/credentials/skdashboard.crt",
                "--tls-keyfile",
                "/run/credentials/skdashboard.key",
                "--owner-policy-file",
                str(tmp_path / "owner-policy.json"),
            ]
        )


def test_callback_access_log_filter_is_sensitive_and_callback_only() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.Logger("test.uvicorn.access")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    callback = (
        "/auth/callback?code=CODE_SENTINEL&state=STATE_SENTINEL"
        "&nonce=NONCE_SENTINEL&code_challenge=PKCE_SENTINEL&token=TOKEN_SENTINEL"
    )
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", callback, "1.1", 400)
    assert "CODE_SENTINEL" in output.getvalue()

    output.seek(0)
    output.truncate()
    handler.addFilter(CallbackAccessLogFilter())
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", callback, "1.1", 400)
    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1",
        "GET",
        "/api/v1/health?failure=UNRELATED_SENTINEL",
        "1.1",
        503,
    )

    captured = output.getvalue()
    assert 'GET /auth/callback HTTP/1.1" 400' in captured
    for secret in (
        "CODE_SENTINEL",
        "STATE_SENTINEL",
        "NONCE_SENTINEL",
        "PKCE_SENTINEL",
        "TOKEN_SENTINEL",
    ):
        assert secret not in captured
    assert "/api/v1/health?failure=UNRELATED_SENTINEL" in captured
    assert captured.endswith('HTTP/1.1" 503\n')


def test_protected_routes_keep_auth_and_origin_denials(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/overview").status_code == 401
    assert (
        client.get(
            "/api/v1/overview",
            headers={
                "Authorization": "Bearer test",
                "Origin": "https://public.example",
            },
        ).status_code
        == 403
    )


def _owner_policy_entry(
    *,
    subject: str = CASEY_FINGERPRINT,
    acting_principal_id: str = CASEY_FINGERPRINT,
    node_id: str = "chiap08",
    valid_from_offset: timedelta = -timedelta(minutes=5),
    expires_at_offset: timedelta = timedelta(hours=1),
):
    from skcoord.authorized_card_policy import (
        AuthorizedCardPolicyEntryV1,
        AuthorizedCardScopeV1,
    )

    current = datetime.now(timezone.utc)
    return AuthorizedCardPolicyEntryV1.issue(
        subject=subject,
        acting_principal_id=acting_principal_id,
        node_id=node_id,
        scope=AuthorizedCardScopeV1(role="project-manager"),
        valid_from=current + valid_from_offset,
        expires_at=current + expires_at_offset,
    )


def _live_owner_policy_args(
    tmp_path: Path,
    entries: tuple,
    *,
    node_id: str = "chiap08",
    resource_id: str | None = None,
    revision: str | None = None,
    owner_policy_sha256: str | None = None,
) -> list[str]:
    from skcoord.authorized_card_policy import AuthorizedCardPolicyDocumentV1

    entry = entries[0]
    owner_policy = tmp_path / "owner-policy.json"
    if len(entries) == 1:
        policy_payload = AuthorizedCardPolicyDocumentV1(entries=entries).model_dump_json()
    else:
        policy_payload = json.dumps(
            {"entries": [item.model_dump(mode="json") for item in entries]}
        )
    owner_policy.write_text(policy_payload, encoding="utf-8")
    owner_policy.chmod(0o600)
    actual_owner_sha256 = hashlib.sha256(owner_policy.read_bytes()).hexdigest()

    for name in ("session.key", "client.secret"):
        path = tmp_path / name
        value = (
            "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
            if name == "session.key"
            else "non-secret-fixture"
        )
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
    gnupg = tmp_path / "gnupg"
    gnupg.mkdir(mode=0o700)
    signer_passphrase = tmp_path / "signer.passphrase"
    signer_passphrase.write_bytes(b"bounded test passphrase")
    signer_passphrase.chmod(0o600)
    trusted = tmp_path / "trusted-issuers.json"
    trusted.write_text('{"value_free":true}', encoding="ascii")
    trusted.chmod(0o600)
    trusted_sha256 = hashlib.sha256(trusted.read_bytes()).hexdigest()
    trusted_signature = tmp_path / "trusted-issuers.json.asc"
    trusted_signature.write_text("test signature", encoding="ascii")
    trusted_signature.chmod(0o600)
    trusted_signature_sha256 = hashlib.sha256(trusted_signature.read_bytes()).hexdigest()
    revisions = tmp_path / "revisions.json"
    revisions.write_text(
        json.dumps(
            {
                "issuer": trusted_sha256,
                "principal": "2" * 64,
                "acting_principal": "3" * 64,
                "revocation": "4" * 64,
                "owner": revision or entry.owner_policy_revision,
            }
        ),
        encoding="utf-8",
    )
    revisions.chmod(0o600)

    return [
        "--home",
        str(tmp_path),
        "--host",
        "10.0.0.139",
        "--tls-certfile",
        "/run/credentials/skdashboard.crt",
        "--tls-keyfile",
        "/run/credentials/skdashboard.key",
        "--session-db",
        str(tmp_path / "sessions.db"),
        "--session-key-file",
        str(tmp_path / "session.key"),
        "--oidc-issuer",
        "https://issuer.example",
        "--oidc-redirect-uri",
        f"{LAN_ORIGIN}/auth/callback",
        "--oidc-client-secret-file",
        str(tmp_path / "client.secret"),
        "--legacy-board-url",
        "https://legacy.example/board",
        "--authorized-resource-id",
        resource_id or entry.resource_id,
        "--owner-policy-file",
        str(owner_policy),
        "--owner-policy-sha256",
        owner_policy_sha256 or actual_owner_sha256,
        "--owner-policy-revision",
        revision or entry.owner_policy_revision,
        "--tenant-id",
        "platform",
        "--node-id",
        node_id,
        "--operator-session-db",
        str(tmp_path / "operator-sessions.db"),
        "--operator-policy-revisions-file",
        str(revisions),
        "--operator-policy-revisions-sha256",
        hashlib.sha256(revisions.read_bytes()).hexdigest(),
        "--signer-fingerprint",
        "DCE38ED7BC9D95D724B5FE7FECF9D6A423EC83F5",
        "--signer-gnupg-home",
        str(gnupg),
        "--signer-passphrase-file",
        str(signer_passphrase),
        "--trusted-issuer-policy-file",
        str(trusted),
        "--trusted-issuer-policy-sha256",
        trusted_sha256,
        "--trusted-issuer-signature-file",
        str(trusted_signature),
        "--trusted-issuer-signature-sha256",
        trusted_signature_sha256,
        "--capability-state-db",
        str(tmp_path / "capability-state.db"),
        "--capability-authorizer-factory",
        "skdashboard.runtime_authorizer:build",
    ]


def test_owner_policy_selection_accepts_casey_fingerprint(tmp_path: Path, monkeypatch) -> None:
    """Casey's exact current policy subject is the runtime principal."""
    from types import SimpleNamespace

    observed = {}
    monkeypatch.setattr(
        "skdashboard.live_control_plane.compose_file_backed_live_control_plane",
        lambda **values: (
            observed.update(composition=values)
            or SimpleNamespace(
                decision_authorizer="typed-authorizer",
                invocation_factory="invocation-factory",
                project_provider="durable-provider",
                schedule_provider="schedule-provider",
                reliability_provider="reliability-provider",
                session_authorizer="in-process-authorizer",
                legacy_board_url="https://legacy.example/board",
            )
        ),
    )
    monkeypatch.setattr(
        "skdashboard.session_adapter.EncryptedSessionAdapter",
        lambda *args, **kwargs: SimpleNamespace(resolve="session-resolver", routes=lambda: []),
    )
    monkeypatch.setattr(
        "skdashboard.runtime_authorizer.build",
        lambda **values: observed.update(authorizer=values) or "durable-authorizer",
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: observed.update(app=app))

    entry = _owner_policy_entry()
    main(_live_owner_policy_args(tmp_path, (entry,)))

    assert observed["authorizer"]["principals"][0].principal_id == CASEY_FINGERPRINT
    assert observed["authorizer"]["principals"][0].subject == CASEY_FINGERPRINT


def test_owner_policy_selection_authorizes_all_active_human_principals(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    observed = {}
    monkeypatch.setattr(
        "skdashboard.live_control_plane.compose_file_backed_live_control_plane",
        lambda **values: (
            observed.update(composition=values)
            or SimpleNamespace(
                decision_authorizer="typed-authorizer",
                invocation_factory="invocation-factory",
                project_provider="durable-provider",
                schedule_provider="schedule-provider",
                reliability_provider="reliability-provider",
                session_authorizer="in-process-authorizer",
                legacy_board_url="https://legacy.example/board",
            )
        ),
    )
    monkeypatch.setattr(
        "skdashboard.session_adapter.EncryptedSessionAdapter",
        lambda *args, **kwargs: SimpleNamespace(resolve="session-resolver", routes=lambda: []),
    )
    monkeypatch.setattr(
        "skdashboard.runtime_authorizer.build",
        lambda **values: observed.update(authorizer=values) or "durable-authorizer",
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: observed.update(app=app))
    casey = _owner_policy_entry()
    jarvis = _owner_policy_entry(
        subject=JARVIS_FINGERPRINT, acting_principal_id=JARVIS_FINGERPRINT
    )

    main(_live_owner_policy_args(tmp_path, (casey, jarvis)))

    assert {principal.subject for principal in observed["authorizer"]["principals"]} == {
        CASEY_FINGERPRINT,
        JARVIS_FINGERPRINT,
    }
    assert observed["composition"]["owner_policy_entries"] == {
        CASEY_FINGERPRINT: casey,
        JARVIS_FINGERPRINT: jarvis,
    }


@pytest.mark.parametrize(
    "case",
    (
        "mismatched-subject",
        "duplicate-current",
        "wrong-node",
        "wrong-resource",
        "wrong-revision",
        "expired",
        "future",
        "hash-drift",
    ),
)
def test_owner_policy_selection_rejects_invalid_current_entry(
    tmp_path: Path, capsys, case: str
) -> None:
    """Every ambiguous, stale, drifted, or mismatched policy fails closed."""
    entry_kwargs = {}
    args_kwargs = {}
    if case == "mismatched-subject":
        entry_kwargs["acting_principal_id"] = JARVIS_FINGERPRINT
    elif case == "wrong-node":
        entry_kwargs["node_id"] = "wrong-node"
    elif case == "expired":
        entry_kwargs.update(
            valid_from_offset=-timedelta(hours=2),
            expires_at_offset=-timedelta(hours=1),
        )
    elif case == "future":
        entry_kwargs.update(
            valid_from_offset=timedelta(hours=1),
            expires_at_offset=timedelta(hours=2),
        )

    entry = _owner_policy_entry(**entry_kwargs)
    entries = (entry, entry) if case == "duplicate-current" else (entry,)
    if case == "wrong-resource":
        args_kwargs["resource_id"] = "authorized-card-set:sha256:" + "b" * 64
    elif case == "wrong-revision":
        args_kwargs["revision"] = "b" * 64
    elif case == "hash-drift":
        args_kwargs["owner_policy_sha256"] = "b" * 64

    with pytest.raises(SystemExit) as exc_info:
        main(_live_owner_policy_args(tmp_path, entries, **args_kwargs))
    assert exc_info.value.code == 2
    assert "runtime policy composition is unavailable" in capsys.readouterr().err
