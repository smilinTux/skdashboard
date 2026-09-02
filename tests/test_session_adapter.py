import asyncio
import os
import ssl
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from starlette.requests import Request
from starlette.testclient import TestClient

from skdashboard.read_only import create_read_only_app
from skdashboard.session_adapter import (
    COOKIE_NAME,
    EncryptedSessionAdapter,
    OIDCClient,
    OIDCExchangeError,
    SessionConfig,
    _digest,
)

ORIGIN = "https://10.0.0.139:7778"


class Tokens:
    def __init__(self):
        self.calls = []

    async def exchange(self, values, *, expected_nonce=None):
        self.calls.append((values, expected_nonce))
        suffix = str(len(self.calls))
        return {
            "access_token": "access-" + suffix,
            "refresh_token": "refresh-" + suffix,
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": "skdashboard.read skdashboard.events.read",
        }

    async def revoke(self, refresh_token):
        self.revoked = refresh_token


class SubjectTokens(Tokens):
    async def exchange(self, values, *, expected_nonce=None):
        token = await super().exchange(values, expected_nonce=expected_nonce)
        token["_verified_subject"] = "operator@example.test"
        return token


def adapter(tmp_path: Path, clock=lambda: 1_000, tokens=None):
    key = tmp_path / "session.key"
    key.write_bytes(Fernet.generate_key())
    os.chmod(key, 0o600)
    return EncryptedSessionAdapter(
        tmp_path / "state" / "sessions.db",
        key,
        SessionConfig(
            issuer="https://capauth.example",
            redirect_uri=f"{ORIGIN}/auth/callback",
            client_secret="test-secret",
        ),
        oidc_client=tokens or Tokens(),
        clock=clock,
    )


def login(client, return_to=None):
    start = client.get(
        "/auth/login",
        params={"return_to": return_to} if return_to is not None else None,
        follow_redirects=False,
    )
    query = parse_qs(urlparse(start.headers["location"]).query)
    return query, client.get(
        "/auth/callback",
        params={"state": query["state"][0], "code": "one-use-code"},
        follow_redirects=False,
    )


def test_session_routes_are_opt_in_and_cookie_is_opaque(tmp_path):
    disabled = TestClient(create_read_only_app(tmp_path), base_url=ORIGIN)
    assert disabled.get("/auth/login").status_code == 404
    assert "Short-lived CapAuth bearer" in disabled.get("/").text
    session = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    assert "Sign in" in client.get("/").text
    assert "Short-lived CapAuth bearer" not in client.get("/").text
    query, response = login(client)
    assert response.status_code == 303
    assert query["code_challenge_method"] == ["S256"]
    assert query["issuer"] == ["https://capauth.example"]
    assert session.oidc.calls[0][1] == query["nonce"][0]
    cookie = response.headers["set-cookie"]
    assert COOKIE_NAME in cookie and "Secure" in cookie and "HttpOnly" in cookie
    assert "SameSite=strict" in cookie and "Domain=" not in cookie
    assert "access-" not in cookie and "refresh-" not in cookie
    data = (tmp_path / "state" / "sessions.db").read_bytes()
    assert b"access-1" not in data and b"refresh-1" not in data and b"test-secret" not in data


def test_app_pages_show_shared_capauth_status_and_identity(tmp_path):
    session = adapter(tmp_path, tokens=SubjectTokens())
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)

    for path in ("/", "/control-plane/portfolio", "/fleet-chat"):
        page = client.get(path)
        assert page.status_code == 200
        assert '<script src="/static/js/auth_status.js"></script>' in page.text
    assert client.get("/static/js/auth_status.js").status_code == 200

    _query, response = login(client, "/fleet-chat")
    assert response.status_code == 303
    assert response.headers["location"] == "/fleet-chat"
    current = client.get("/auth/session")
    assert current.status_code == 200
    assert current.json()["authenticator"] == "CapAuth"
    assert current.json()["subject"] == "operator@example.test"
    assert current.json()["expires_at"] == 29_800


def test_auth_status_uses_compact_native_account_disclosure(tmp_path):
    session = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)

    asset = client.get("/static/js/auth_status.js")
    assert asset.status_code == 200
    assert '<details id="sk-auth-menu">' in asset.text
    assert "Login with CapAuth" in asset.text
    assert "Logout" in asset.text
    assert "menu.open = false" in asset.text


def test_login_returns_once_to_original_portfolio_query(tmp_path):
    target = (
        "/control-plane/portfolio?role=project-manager&scope=estate&window=latest"
        "&baseline=none&service=all"
    )
    session = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)

    query, response = login(client, target)

    assert response.status_code == 303
    assert response.headers["location"] == target
    assert query["redirect_uri"] == [f"{ORIGIN}/auth/callback"]
    data = session.database.read_bytes()
    assert target.encode() not in data


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/control-plane/now",
        "//evil.example/control-plane/now",
        "https://user:password@evil.example/control-plane/now",
        "/control-plane/../board",
        "/control-plane/%2e%2e/board",
        "/control-plane/now\\@evil.example",
        "/control-plane/now\n?role=project-manager",
        "/control-plane/now#fragment",
        "/control-plane/now?authorization=secret",
        "/control-plane/now?role=one&role=two",
        "/board",
        "/control-plane/now?role=" + "x" * 257,
        "/control-plane/now?role=" + "x" * 1024,
    ],
)
def test_login_ignores_unsafe_return_targets(tmp_path, target):
    session = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)

    _query, response = login(client, target)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_state_is_one_use_and_restart_recovers_session(tmp_path):
    tokens = Tokens()
    first = adapter(tmp_path, tokens=tokens)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=first), base_url=ORIGIN)
    query, response = login(client)
    assert response.status_code == 303
    assert (
        client.get(
            "/auth/callback", params={"state": query["state"][0], "code": "again"}
        ).status_code
        == 400
    )
    second = EncryptedSessionAdapter(
        first.database,
        tmp_path / "session.key",
        first.config,
        oidc_client=tokens,
        clock=lambda: 1_001,
    )
    calls = []
    app = create_read_only_app(
        tmp_path,
        session_adapter=second,
        authorizer=lambda bearer, capability, target: (
            calls.append((bearer, capability, target)) or True
        ),
    )
    restarted = TestClient(app, base_url=ORIGIN)
    restarted.cookies.update(client.cookies)
    assert restarted.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200
    assert calls == [("access-1", "skdashboard.read", "/api/v1/overview")]


def test_refresh_rotates_server_credentials_and_pep_runs_each_request(tmp_path):
    now = [1_000]
    tokens = Tokens()
    session = adapter(tmp_path, clock=lambda: now[0], tokens=tokens)
    calls = []
    client = TestClient(
        create_read_only_app(
            tmp_path,
            session_adapter=session,
            authorizer=lambda bearer, *_: calls.append(bearer) or True,
        ),
        base_url=ORIGIN,
    )
    login(client)
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200
    now[0] = 1_290
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200
    assert calls == ["access-1", "access-2"]
    assert tokens.calls[-1] == (
        {"grant_type": "refresh_token", "refresh_token": "refresh-1"},
        None,
    )


def test_session_capability_issuer_mints_fresh_internal_bearers(tmp_path):
    session = adapter(tmp_path)
    minted = []

    async def issuer(_request, resolved, capability, target):
        assert resolved.access_token == "access-1"
        minted.append((capability, target))
        return f"internal-{len(minted)}"

    seen = []
    client = TestClient(
        create_read_only_app(
            tmp_path,
            session_adapter=session,
            session_capability_issuer=issuer,
            authorizer=lambda bearer, *_: seen.append(bearer) or True,
        ),
        base_url=ORIGIN,
    )
    login(client)
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200
    assert minted == [
        ("skdashboard.read", "/api/v1/overview"),
        ("skdashboard.read", "/api/v1/overview"),
    ]
    assert seen == ["internal-1", "internal-2"]


def test_session_capability_issuer_cannot_reuse_session_access_token(tmp_path):
    session = adapter(tmp_path)
    client = TestClient(
        create_read_only_app(
            tmp_path,
            session_adapter=session,
            session_capability_issuer=lambda _request, resolved, *_: resolved.access_token,
            authorizer=lambda *_: True,
        ),
        base_url=ORIGIN,
    )
    login(client)
    response = client.get("/api/v1/overview", headers={"Origin": ORIGIN})
    assert response.status_code == 401


def test_session_capability_issuer_rejects_browser_bearer_and_absent_session(tmp_path):
    session = adapter(tmp_path)
    minted = []
    client = TestClient(
        create_read_only_app(
            tmp_path,
            session_adapter=session,
            session_capability_issuer=lambda *_: minted.append(True) or "internal",
            authorizer=lambda *_: True,
        ),
        base_url=ORIGIN,
    )
    assert (
        client.get(
            "/api/v1/overview",
            headers={"Origin": ORIGIN, "Authorization": "Bearer browser-token"},
        ).status_code
        == 401
    )
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 401
    assert minted == []


def test_control_plane_bridge_persists_only_an_opaque_handle(tmp_path):
    class Bridge:
        def enroll(self, subject, origin):
            assert subject == "operator@example.test"
            assert origin == ORIGIN
            return "opaque-control-plane-handle-" + "a" * 32

        def request(self, handle, _request):
            assert handle == "opaque-control-plane-handle-" + "a" * 32
            return object()

        def discard(self, _handle):
            return None

    session = adapter(tmp_path, tokens=SubjectTokens())
    session.control_plane_bridge = Bridge()
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)

    login(client)
    handle = client.cookies.get(COOKIE_NAME)
    with session._connect() as connection:
        row = connection.execute(
            "SELECT encrypted FROM sessions WHERE handle_hash = ?", (_digest(handle),)
        ).fetchone()
    record = session._open(row["encrypted"])

    assert record["control_plane_session"] == "opaque-control-plane-handle-" + "a" * 32
    assert isinstance(record["control_plane_session"], str)


def test_control_plane_bridge_rejects_serializable_proof_material(tmp_path):
    class UnsafeBridge:
        def enroll(self, *_args):
            return {"cookie": "raw-cookie", "csrf": "raw-csrf", "device": "raw-device"}

    session = adapter(tmp_path, tokens=SubjectTokens())
    session.control_plane_bridge = UnsafeBridge()
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)

    _query, response = login(client)

    assert response.status_code == 503
    assert response.json()["error"] == "authentication_unavailable"


def test_process_restart_expires_session_when_bridge_proof_is_gone(tmp_path):
    from types import SimpleNamespace

    from test_control_plane_decision_context import Signer

    from skdashboard.live_control_plane import InProcessOperatorBridge, LiveControlPlaneConfig

    class OperatorSessions:
        def create(self, **_values):
            return SimpleNamespace(take=lambda: ("operator-cookie", "operator-csrf"))

    def bridge():
        return InProcessOperatorBridge(
            config=LiveControlPlaneConfig(
                legacy_board_url="https://legacy.example/board",
                resource_id="authorized-card-set:sha256:" + "a" * 64,
                owner_policy_revision="b" * 64,
                tenant_id="platform",
            ),
            sessions=OperatorSessions(),
            revisions=object(),
            signer=Signer(),
        )

    first_bridge = bridge()
    first = adapter(tmp_path, tokens=SubjectTokens())
    first.control_plane_bridge = first_bridge
    client = TestClient(create_read_only_app(tmp_path, session_adapter=first), base_url=ORIGIN)
    login(client)
    assert len(first_bridge._proofs) == 1

    restarted_bridge = bridge()
    restarted = EncryptedSessionAdapter(
        first.database,
        tmp_path / "session.key",
        first.config,
        oidc_client=SubjectTokens(),
        control_plane_bridge=restarted_bridge,
        clock=lambda: 1_001,
    )
    app = create_read_only_app(
        tmp_path,
        session_adapter=restarted,
        session_authorizer=restarted_bridge,
    )
    after_restart = TestClient(app, base_url=ORIGIN)
    after_restart.cookies.update(client.cookies)

    response = after_restart.get("/api/v1/overview", headers={"Origin": ORIGIN})

    assert response.status_code == 401
    assert response.json()["message"] == "browser reauthentication is required"
    assert response.headers["set-cookie"].startswith(f'{COOKIE_NAME}=""')
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert COOKIE_NAME not in after_restart.cookies
    assert after_restart.get("/auth/session").status_code == 401
    with restarted._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_legacy_refresh_reservation_without_timestamp_is_recovered(tmp_path):
    now = [1_000]
    tokens = Tokens()
    session = adapter(tmp_path, clock=lambda: now[0], tokens=tokens)
    client = TestClient(
        create_read_only_app(
            tmp_path,
            session_adapter=session,
            authorizer=lambda *_: True,
        ),
        base_url=ORIGIN,
    )
    login(client)
    handle = client.cookies.get(COOKIE_NAME)
    with session._connect() as connection:
        row = connection.execute(
            "SELECT encrypted FROM sessions WHERE handle_hash = ?",
            (_digest(handle),),
        ).fetchone()
        legacy = session._open(row["encrypted"])
        legacy["refreshing"] = True
        legacy.pop("refreshing_at", None)
        connection.execute(
            "UPDATE sessions SET encrypted = ? WHERE handle_hash = ?",
            (session._seal(legacy), _digest(handle)),
        )
    now[0] = 1_300
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{COOKIE_NAME}={handle}".encode())],
        }
    )
    assert asyncio.run(session.resolve(request)).state == "authenticated"
    assert len(tokens.calls) == 2


def test_csrf_logout_and_corrupt_store_fail_closed(tmp_path):
    session = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    info = client.get("/auth/session")
    assert info.status_code == 200
    csrf = info.json()["csrf_token"]
    assert (
        client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "bad"}).status_code
        == 403
    )
    assert (
        client.post(
            "/auth/logout", headers={"Origin": "https://public.example", "X-CSRF-Token": csrf}
        ).status_code
        == 403
    )
    assert (
        client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code
        == 204
    )
    assert client.get("/auth/session").status_code == 401


def test_key_permissions_and_token_response_are_strict(tmp_path):
    key = tmp_path / "bad.key"
    key.write_bytes(Fernet.generate_key())
    os.chmod(key, 0o644)
    try:
        EncryptedSessionAdapter(
            tmp_path / "db", key, SessionConfig("https://i", f"{ORIGIN}/auth/callback", "s")
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("permissive key was accepted")


def test_consistent_encrypted_backup_recovers_after_restart(tmp_path):
    first = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=first), base_url=ORIGIN)
    login(client)
    backup = tmp_path / "backup" / "sessions.db"
    first.backup(backup)
    assert backup.stat().st_mode & 0o077 == 0
    assert b"access-1" not in backup.read_bytes()
    recovered = EncryptedSessionAdapter(
        backup,
        tmp_path / "session.key",
        first.config,
        oidc_client=Tokens(),
        clock=lambda: 1_001,
    )
    restored = TestClient(
        create_read_only_app(tmp_path, session_adapter=recovered, authorizer=lambda *_: True),
        base_url=ORIGIN,
    )
    restored.cookies.update(client.cookies)
    assert restored.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200


def test_login_transactions_are_bounded_and_pruned(tmp_path):
    session = adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    responses = [client.get("/auth/login", follow_redirects=False) for _ in range(17)]
    assert responses[-1].status_code == 429
    with session._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM login_transactions").fetchone()[0] == 16


def test_logout_requires_upstream_revocation(tmp_path):
    tokens = Tokens()
    session = adapter(tmp_path, tokens=tokens)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    assert (
        client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code
        == 204
    )
    assert tokens.revoked == "refresh-1"


def test_only_bounded_session_post_route_is_added(tmp_path):
    app = create_read_only_app(tmp_path, session_adapter=adapter(tmp_path))
    posts = {route.path for route in app.routes if "POST" in (route.methods or set())}
    assert posts == {"/auth/logout"}
    for path in ("/api/card/x/mutate", "/api/assistant", "/api/cmdb/apply"):
        assert TestClient(app, base_url=ORIGIN).post(path).status_code == 404


def test_launcher_session_configuration_is_all_or_nothing(tmp_path, monkeypatch):
    from skdashboard.read_only import main

    key = tmp_path / "session.key"
    secret = tmp_path / "client.secret"
    key.write_bytes(Fernet.generate_key())
    secret.write_text("confidential", encoding="utf-8")
    os.chmod(key, 0o600)
    os.chmod(secret, 0o600)
    observed = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: observed.update(app=app, **kwargs))
    main(
        [
            "--home",
            str(tmp_path / "home"),
            "--host",
            "10.0.0.139",
            "--tls-certfile",
            "/run/cert",
            "--tls-keyfile",
            "/run/key",
            "--session-db",
            str(tmp_path / "state" / "sessions.db"),
            "--session-key-file",
            str(key),
            "--oidc-issuer",
            "https://capauth.example",
            "--oidc-redirect-uri",
            f"{ORIGIN}/auth/callback",
            "--oidc-client-secret-file",
            str(secret),
        ]
    )
    assert any(route.path == "/auth/login" for route in observed["app"].routes)


@pytest.mark.parametrize(
    ("detail", "expected"),
    [("grant_not_current", "grant_not_current"), ("token=must-not-leak", "unknown")],
)
def test_oidc_http_failure_keeps_only_allowlisted_detail(monkeypatch, detail, expected):
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(403, json={"detail": detail}, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = OIDCClient(SessionConfig("https://capauth.example", f"{ORIGIN}/auth/callback", "s"))
    with pytest.raises(OIDCExchangeError) as caught:
        asyncio.run(client.exchange({"grant_type": "authorization_code"}))
    assert (caught.value.category, caught.value.status_code, caught.value.detail) == (
        "upstream_denied",
        403,
        expected,
    )


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        ("tls", "tls_unavailable"),
        ("network", "network_unavailable"),
        ("timeout", "upstream_timeout"),
    ],
)
def test_oidc_transport_failures_are_distinct(monkeypatch, failure, category):
    real_client = httpx.AsyncClient

    def handler(request):
        if failure == "tls":
            try:
                raise ssl.SSLError("certificate failed")
            except ssl.SSLError as exc:
                raise httpx.ConnectError("connect failed", request=request) from exc
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("connect failed", request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = OIDCClient(SessionConfig("https://capauth.example", f"{ORIGIN}/auth/callback", "s"))
    with pytest.raises(OIDCExchangeError, match=category) as caught:
        asyncio.run(client.exchange({"grant_type": "authorization_code"}))
    assert caught.value.category == category


def test_oidc_issuer_and_nonce_failures_are_distinct(monkeypatch):
    real_client = httpx.AsyncClient
    issuer = "https://capauth.example"
    metadata_issuer = ["https://wrong.example"]

    def handler(request):
        if request.url.path == "/oidc/token":
            return httpx.Response(200, json={"id_token": "opaque"}, request=request)
        if request.url.path == "/oidc/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": metadata_issuer[0], "jwks_uri": issuer + "/oidc/jwks.json"},
                request=request,
            )
        return httpx.Response(200, json={"keys": [{"kid": "one"}]}, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = OIDCClient(SessionConfig(issuer, f"{ORIGIN}/auth/callback", "s"))
    with pytest.raises(OIDCExchangeError, match="issuer_mismatch"):
        asyncio.run(client.exchange({"grant_type": "authorization_code"}, expected_nonce="n"))

    metadata_issuer[0] = issuer
    monkeypatch.setattr(jwt, "get_unverified_header", lambda _token: {"kid": "one"})
    monkeypatch.setattr(jwt.PyJWK, "from_dict", lambda _key: type("Key", (), {"key": object()})())
    monkeypatch.setattr(jwt, "decode", lambda *_args, **_kwargs: {"nonce": "wrong"})
    with pytest.raises(OIDCExchangeError, match="nonce_mismatch"):
        asyncio.run(client.exchange({"grant_type": "authorization_code"}, expected_nonce="n"))


def test_oidc_uses_capauth_idp_discovery_not_legacy_pgp_discovery(monkeypatch):
    real_client = httpx.AsyncClient
    issuer = "https://capauth.example"
    requested_paths = []

    def handler(request):
        requested_paths.append(request.url.path)
        if request.url.path == "/oidc/token":
            return httpx.Response(200, json={"id_token": "opaque"}, request=request)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": "https://legacy-pgp.example", "jwks_uri": issuer + "/jwks"},
                request=request,
            )
        if request.url.path == "/oidc/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": issuer, "jwks_uri": issuer + "/oidc/jwks.json"},
                request=request,
            )
        return httpx.Response(200, json={"keys": [{"kid": "one"}]}, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(jwt, "get_unverified_header", lambda _token: {"kid": "one"})
    monkeypatch.setattr(jwt.PyJWK, "from_dict", lambda _key: type("Key", (), {"key": object()})())
    monkeypatch.setattr(jwt, "decode", lambda *_args, **_kwargs: {"nonce": "n"})

    client = OIDCClient(SessionConfig(issuer, f"{ORIGIN}/auth/callback", "s"))
    result = asyncio.run(client.exchange({"grant_type": "authorization_code"}, expected_nonce="n"))

    assert result == {"id_token": "opaque"}
    assert "/oidc/.well-known/openid-configuration" in requested_paths
    assert "/.well-known/openid-configuration" not in requested_paths


def test_callback_returns_reference_and_logs_only_safe_denial(tmp_path, caplog):
    class DeniedTokens(Tokens):
        async def exchange(self, values, *, expected_nonce=None):
            raise OIDCExchangeError("upstream_denied", status_code=403, detail="grant_not_current")

    session = adapter(tmp_path, tokens=DeniedTokens())
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    with caplog.at_level("WARNING", logger="skdashboard.session_adapter"):
        query, response = login(client)
    body = response.json()
    assert response.status_code == 403
    assert body["error"] == "authentication_denied"
    assert len(body["reference"]) == 16
    assert body["reference"] in caplog.text
    assert "category=upstream_denied status=403 detail=grant_not_current" in caplog.text
    assert query["state"][0] not in caplog.text
    assert "one-use-code" not in caplog.text
    assert "test-secret" not in caplog.text
    assert response.headers["cache-control"] == "no-store"
