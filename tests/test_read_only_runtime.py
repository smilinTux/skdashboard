import io
import logging
from pathlib import Path

from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from skdashboard.read_only import (
    ALLOWED_BIND_HOSTS,
    HSTS_POLICY,
    CallbackAccessLogFilter,
    create_read_only_app,
    main,
)

LAN_ORIGIN = "https://10.0.0.139:7778"
TAILNET_ORIGIN = "https://100.81.238.58:7778"


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
    assert client.get("/.well-known/skworld-module.json").status_code == 200
    manifest = client.get("/.well-known/skworld-module.json").json()
    assert manifest["health"].endswith("/api/v1/health")
    assert manifest["auth"] == {"audience": "skdashboard", "scopes": ["skdashboard.read"]}
    assert "operator" not in manifest
    assert client.get("/api/v1/health").status_code == 200
    headers = {"Authorization": "Bearer test", "Origin": LAN_ORIGIN}
    assert client.get("/api/v1/overview", headers=headers).status_code == 200
    assert client.get("/metrics", headers=headers).status_code == 200
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


def test_exact_https_origins_redirect_hsts_and_public_host_denial(tmp_path: Path) -> None:
    app = create_read_only_app(tmp_path, authorizer=lambda *_: True)
    for origin in (LAN_ORIGIN, TAILNET_ORIGIN):
        response = TestClient(app, base_url=origin).get("/api/v1/health")
        assert response.status_code == 200
        assert response.headers["strict-transport-security"] == HSTS_POLICY

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

    observed = {}
    factory_module = tmp_path / "approved_factory.py"
    factory_module.write_text("def build():\n    return object()\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        "skdashboard.live_control_plane.compose_file_backed_live_control_plane",
        lambda **values: (
            observed.update(composition=values)
            or SimpleNamespace(
                decision_authorizer="typed-authorizer",
                invocation_factory="invocation-factory",
                project_provider="durable-provider",
                session_capability_issuer="ephemeral-issuer",
                legacy_board_url="https://legacy.example/board",
            )
        ),
    )
    monkeypatch.setattr(
        "skdashboard.session_adapter.EncryptedSessionAdapter",
        lambda *args, **kwargs: SimpleNamespace(resolve="session-resolver", routes=lambda: []),
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: observed.update(app=app))
    for name in ("session.key", "client.secret"):
        path = tmp_path / name
        path.write_text("non-secret-fixture", encoding="utf-8")
        path.chmod(0o600)

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
            "--issuer-socket",
            str(tmp_path / "issuer.sock"),
            "--legacy-board-url",
            "https://legacy.example/board",
            "--authorized-resource-id",
            "authorized-card-set:sha256:" + "a" * 64,
            "--owner-policy-file",
            str(tmp_path / "owner-policy.json"),
            "--owner-policy-revision",
            "b" * 64,
            "--capability-authorizer-factory",
            "approved_factory:build",
        ]
    )

    assert observed["composition"]["owner_policy_file"] == tmp_path / "owner-policy.json"
    assert observed["composition"]["capability_authorizer"].__class__ is object
    routes = {route.path for route in observed["app"].routes}
    assert {"/control-plane/now", "/control-plane/portfolio", "/control-plane/schedule"} <= routes
    assert observed["app"].routes


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
            headers={"Authorization": "Bearer test", "Origin": "https://public.example"},
        ).status_code
        == 403
    )
