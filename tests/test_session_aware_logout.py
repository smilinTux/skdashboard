"""Session-aware logout control tests for the read-only shell."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from skdashboard.read_only import create_read_only_app
from skdashboard.session_adapter import (
    COOKIE_NAME,
    EncryptedSessionAdapter,
    SessionConfig,
    _digest,
)

ORIGIN = "https://10.0.0.139:7778"


class MockOIDCClient:
    def __init__(self):
        self.calls = []
        self.revoked = None

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


def create_session_adapter(tmp_path: Path, clock=lambda: 1_000):
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
        oidc_client=MockOIDCClient(),
        clock=clock,
    )


def login(client):
    start = client.get("/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    return query, client.get(
        "/auth/callback",
        params={"state": query["state"][0], "code": "one-use-code"},
        follow_redirects=False,
    )


def test_signed_in_shows_sign_out_and_hides_sign_in(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    html = client.get("/").text
    # Both buttons exist in HTML with initial hidden state, JS toggles based on session
    assert 'id="signout-button"' in html
    assert 'id="signin-button"' in html
    assert "class=\"hidden\"" in html  # Both start hidden
    assert "Sign out" in html
    assert "Sign in" in html
    # Session endpoint confirms authenticated state for JS to toggle visibility
    session_response = client.get("/auth/session")
    assert session_response.status_code == 200
    assert session_response.json()["authenticated"] is True
    assert "csrf_token" in session_response.json()


def test_signed_out_shows_sign_in_and_hides_sign_out(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    html = client.get("/").text
    # Both buttons exist in HTML with initial hidden state, JS toggles based on session
    assert 'id="signout-button"' in html
    assert 'id="signin-button"' in html
    assert "class=\"hidden\"" in html  # Both start hidden
    assert "Sign out" in html
    assert "Sign in" in html
    # Session endpoint confirms unauthenticated state for JS to toggle visibility
    session_response = client.get("/auth/session")
    assert session_response.status_code == 401
    assert session_response.json()["authenticated"] is False


def test_session_endpoint_returns_csrf_token_for_authenticated_user(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    response = client.get("/auth/session")
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is True
    assert "csrf_token" in data
    assert len(data["csrf_token"]) >= 32


def test_session_endpoint_returns_unauthenticated_for_no_session(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    response = client.get("/auth/session")
    assert response.status_code == 401
    data = response.json()
    assert data["authenticated"] is False


def test_session_endpoint_returns_unauthenticated_for_expired_session(tmp_path):
    now = [1_000]
    session = create_session_adapter(tmp_path, clock=lambda: now[0])
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    now[0] = 1_000_000
    response = client.get("/auth/session")
    assert response.status_code == 401
    data = response.json()
    assert data["authenticated"] is False


def test_logout_with_valid_csrf_and_origin_succeeds(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    response = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert "set-cookie" in response.headers
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert session.oidc.revoked == "refresh-1"


def test_logout_with_invalid_csrf_fails_closed(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    response = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "invalid"})
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "forbidden"
    assert session.oidc.revoked is None


def test_logout_with_missing_csrf_fails_closed(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    response = client.post("/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "forbidden"
    assert session.oidc.revoked is None


def test_logout_with_wrong_origin_fails_closed(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    response = client.post(
        "/auth/logout",
        headers={"Origin": "https://malicious.example", "X-CSRF-Token": csrf}
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "forbidden"
    assert session.oidc.revoked is None


def test_logout_with_no_session_fails_closed(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    response = client.post(
        "/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": "any-token"}
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "forbidden"


def test_after_logout_session_endpoint_returns_unauthenticated(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    response = client.get("/auth/session")
    assert response.status_code == 401
    data = response.json()
    assert data["authenticated"] is False


def test_after_logout_overview_requires_reauth(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    response = client.get("/api/v1/overview", headers={"Origin": ORIGIN})
    assert response.status_code == 401


def test_expired_session_shows_sign_in_and_hides_sign_out(tmp_path):
    now = [1_000]
    session = create_session_adapter(tmp_path, clock=lambda: now[0])
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    now[0] = 1_000_000
    html = client.get("/").text
    # Both buttons exist in HTML with initial hidden state, JS toggles based on session
    assert 'id="signout-button"' in html
    assert 'id="signin-button"' in html
    assert "class=\"hidden\"" in html  # Both start hidden
    # Session endpoint confirms expired state for JS to toggle visibility
    session_response = client.get("/auth/session")
    assert session_response.status_code == 401
    assert session_response.json()["authenticated"] is False


def test_html_loads_and_shows_correct_state_on_page_load(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    html = client.get("/").text
    assert "checkSession" in html
    assert "signout-button" in html
    assert "signin-button" in html
    assert "Session check pending" in html


def test_no_redirect_loop_on_repeated_logout_attempts(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    first = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert first.status_code == 204
    second = client.post(
        "/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert second.status_code == 403


def test_csrf_token_changes_on_new_session(tmp_path):
    session = create_session_adapter(tmp_path)
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    first_csrf = client.get("/auth/session").json()["csrf_token"]
    client.get("/auth/login", follow_redirects=False)
    login(client)
    second_csrf = client.get("/auth/session").json()["csrf_token"]
    assert first_csrf != second_csrf


def test_protected_overview_unauthorized_after_upstream_revocation(tmp_path):
    class RevokingClient:
        def __init__(self):
            self.revoke_called = False

        async def exchange(self, values, *, expected_nonce=None):
            suffix = "1"
            return {
                "access_token": "access-" + suffix,
                "refresh_token": "refresh-" + suffix,
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": "skdashboard.read skdashboard.events.read",
            }

        async def revoke(self, refresh_token):
            self.revoke_called = True

    key = tmp_path / "key.key"
    key.write_bytes(Fernet.generate_key())
    os.chmod(key, 0o600)
    oidc = RevokingClient()
    session = EncryptedSessionAdapter(
        tmp_path / "sessions.db",
        key,
        SessionConfig("https://capauth.example", f"{ORIGIN}/auth/callback", "secret"),
        oidc_client=oidc,
    )
    client = TestClient(create_read_only_app(tmp_path, session_adapter=session), base_url=ORIGIN)
    login(client)
    csrf = client.get("/auth/session").json()["csrf_token"]
    response = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert oidc.revoke_called is True
    assert client.get("/api/v1/overview", headers={"Origin": ORIGIN}).status_code == 401
