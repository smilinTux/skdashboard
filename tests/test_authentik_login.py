from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from skdashboard import auth_setup, read_only
from skdashboard.auth_setup import Config, Layout, render_sso_unit
from skdashboard.authentik_authz import AuthentikAuthorizer
from skdashboard.read_only import create_read_only_app
from skdashboard.session_adapter import (
    AUTHENTIK_SCOPES,
    EncryptedSessionAdapter,
    OIDCClient,
    SessionConfig,
)

ISSUER = "https://sso.example/application/o/skdashboard/"
ORIGIN = "https://10.0.0.139:7778"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class JwkClient:
    def __init__(self, key=KEY):
        self.key = key

    def get_signing_key_from_jwt(self, _token):
        return type("Signing", (), {"key": self.key.public_key()})


def token(groups=None, *, key=KEY, lifetime=300, aud="skdashboard", iss=ISSUER, exp_offset=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": "chef", "iat": now}
    claims["exp"] = now + (lifetime if exp_offset is None else exp_offset)
    if groups is not None:
        claims["groups"] = groups
    return jwt.encode(claims, key, algorithm="RS256")


def authorizer():
    return AuthentikAuthorizer(
        ISSUER,
        "skdashboard",
        ["skdashboard-admin", "skadmin"],
        ["skdashboard-users"],
        jwk_client=JwkClient(),
    )


@pytest.mark.parametrize("group", ["skdashboard-admin", "skadmin"])
def test_admin_groups_get_every_dashboard_capability(group):
    check = authorizer()
    assert check(token([group]), "skdashboard.read", "/x")
    assert check(token([group]), "skdashboard.reports.deliver.simulate", "/x")
    assert not check(token([group]), "skgateway.admin", "/x")


def test_user_group_gets_read_capabilities_only():
    check = authorizer()
    assert check(token(["skdashboard-users"]), "skdashboard.read", "/x")
    assert check(token(["skdashboard-users"]), "skdashboard.events.read", "/x")
    assert not check(token(["skdashboard-users"]), "skdashboard.reports.deliver.simulate", "/x")


@pytest.mark.parametrize(
    "bad",
    [
        lambda: token(["someone-else"]),
        lambda: token([]),
        lambda: token(None),
        lambda: token("skadmin"),
        lambda: token(["skadmin"], aud="another-app"),
        lambda: token(["skadmin"], iss="https://sso.example/application/o/other/"),
        lambda: token(["skadmin"], exp_offset=-5),
        lambda: token(["skadmin"], lifetime=3600),
        lambda: token(["skadmin"], key=rsa.generate_private_key(65537, 2048)),
        lambda: "not-a-token",
    ],
)
def test_authorizer_denies_everything_else(bad):
    assert authorizer()(bad(), "skdashboard.read", "/x") is False


def test_authorizer_fails_closed_when_keys_are_unavailable():
    class Broken:
        def get_signing_key_from_jwt(self, _token):
            raise httpx.ConnectError("down")

    check = AuthentikAuthorizer(ISSUER, "skdashboard", ["skadmin"], jwk_client=Broken())
    assert check(token(["skadmin"]), "skdashboard.read", "/x") is False


class Tokens:
    def __init__(self, access, scope=AUTHENTIK_SCOPES):
        self.access, self.scope = access, scope

    async def authorization_endpoint(self):
        return "https://sso.example/application/o/authorize/"

    async def exchange(self, values, *, expected_nonce=None):
        return {
            "access_token": self.access,
            "refresh_token": "refresh",
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": self.scope,
            "_verified_subject": "chef",
        }


def adapter(tmp_path, tokens):
    key = tmp_path / "session.key"
    key.write_bytes(Fernet.generate_key())
    os.chmod(key, 0o600)
    return EncryptedSessionAdapter(
        tmp_path / "state" / "sessions.db",
        key,
        SessionConfig(ISSUER, f"{ORIGIN}/auth/callback", "secret", provider="authentik"),
        oidc_client=tokens,
    )


def login(client):
    start = client.get("/auth/login", follow_redirects=False)
    assert start.status_code == 302
    location = urlparse(start.headers["location"])
    query = parse_qs(location.query)
    assert location.path == "/application/o/authorize/"
    assert query["scope"] == [AUTHENTIK_SCOPES]
    assert query["client_id"] == ["skdashboard"]
    assert "issuer" not in query
    return client.get(
        "/auth/callback",
        params={"state": query["state"][0], "code": "code"},
        follow_redirects=False,
    )


def app_client(tmp_path, tokens):
    return TestClient(
        create_read_only_app(
            tmp_path, session_adapter=adapter(tmp_path, tokens), authorizer=authorizer()
        ),
        base_url=ORIGIN,
    )


def test_login_reports_authentik_and_groups_decide_api_access(tmp_path):
    client = app_client(tmp_path, Tokens(token(["skdashboard-users"])))
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 401
    assert login(client).status_code == 303
    session = client.get("/auth/session").json()
    assert session["authenticator"] == "Authentik"
    assert session["subject"] == "chef"
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 200


def test_login_without_a_dashboard_group_cannot_read_data(tmp_path):
    client = app_client(tmp_path, Tokens(token(["someone-else"])))
    assert login(client).status_code == 303
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 403


def test_token_without_profile_scope_is_refused(tmp_path):
    client = app_client(tmp_path, Tokens(token(["skadmin"]), scope="openid offline_access"))
    assert login(client).status_code == 503


def test_login_fails_cleanly_when_authentik_discovery_is_down(tmp_path):
    class Down(Tokens):
        async def authorization_endpoint(self):
            raise httpx.ConnectError("down")

    client = app_client(tmp_path, Down("x"))
    assert client.get("/auth/login", follow_redirects=False).status_code == 503


@pytest.mark.parametrize(
    "kwargs",
    [
        {"issuer": ISSUER.rstrip("/"), "provider": "authentik"},
        {"issuer": "http://sso.example/application/o/x/", "provider": "authentik"},
        {"issuer": ISSUER, "provider": "capauth"},
        {"issuer": "https://sso.example", "provider": "other"},
    ],
)
def test_session_config_rejects_wrong_issuer_shape(kwargs):
    with pytest.raises(ValueError):
        SessionConfig(redirect_uri=f"{ORIGIN}/auth/callback", client_secret="s", **kwargs)


def test_oidc_client_uses_discovered_endpoints_and_checks_issuer(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": "https://sso.example/application/o/authorize/",
                    "token_endpoint": "https://sso.example/application/o/token/",
                    "revocation_endpoint": "https://sso.example/application/o/revoke/",
                },
            )
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "skdashboard.session_adapter.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    client = OIDCClient(SessionConfig(ISSUER, f"{ORIGIN}/cb", "s", provider="authentik"))
    assert (
        asyncio.run(client.authorization_endpoint())
        == "https://sso.example/application/o/authorize/"
    )
    asyncio.run(client.exchange({"grant_type": "refresh_token", "refresh_token": "r"}))
    asyncio.run(client.revoke("r"))
    assert seen[0] == f"{ISSUER}.well-known/openid-configuration"
    assert seen[1:] == [
        "https://sso.example/application/o/token/",
        "https://sso.example/application/o/revoke/",
    ]


def launcher_args(tmp_path, *extra):
    return [
        "--home", str(tmp_path), "--host", "127.0.0.1",
        "--tls-certfile", str(tmp_path / "c"), "--tls-keyfile", str(tmp_path / "k"),
        "--oidc-provider", "authentik", *extra,
    ]  # fmt: skip


def test_runtime_refuses_authentik_without_session_or_admin_group(tmp_path):
    with pytest.raises(SystemExit):
        read_only.main(launcher_args(tmp_path, "--authentik-admin-group", "skadmin"))
    with pytest.raises(SystemExit):
        read_only.main(
            [
                "--home", str(tmp_path), "--host", "127.0.0.1",
                "--tls-certfile", "c", "--tls-keyfile", "k",
                "--authentik-admin-group", "skadmin",
            ]  # fmt: skip
        )


def test_runtime_refuses_mixing_authentik_with_capauth_authorization(tmp_path):
    secret = tmp_path / "s"
    secret.write_text("x")
    os.chmod(secret, 0o600)
    with pytest.raises(SystemExit):
        read_only.main(
            launcher_args(
                tmp_path,
                "--authentik-admin-group", "skadmin",
                "--capauth-home", str(tmp_path),
                "--session-db", str(tmp_path / "db"),
                "--session-key-file", str(secret),
                "--oidc-issuer", ISSUER,
                "--oidc-redirect-uri", f"{ORIGIN}/auth/callback",
                "--oidc-client-secret-file", str(secret),
            )  # fmt: skip
        )


def test_sso_unit_is_separate_from_the_capauth_dashboard(tmp_path):
    cfg = Config(hostname="node.example.ts.net", bind="100.64.0.9", agent_home="/h")
    layout = Layout(tmp_path)
    unit = render_sso_unit(
        layout,
        cfg,
        issuer=ISSUER,
        port=7780,
        admin_groups=["skdashboard-admin", "skadmin"],
        user_groups=["skdashboard-users"],
    )
    assert "--oidc-provider authentik" in unit
    assert f"--oidc-issuer {ISSUER}" in unit
    assert "--oidc-redirect-uri https://node.example.ts.net:7780/auth/callback" in unit
    assert "--authentik-admin-group skadmin" in unit
    assert "--authentik-user-group skdashboard-users" in unit
    assert "Environment=SKDASHBOARD_ALLOWED_BROWSER_ORIGINS=https://node.example.ts.net:7780" in unit
    assert "--capauth-home" not in unit
    assert str(layout.session_db) not in unit
    assert str(layout.client_secret) not in unit
    assert auth_setup.SSO_UNIT != auth_setup.DASHBOARD_UNIT
