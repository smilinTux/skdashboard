from __future__ import annotations

import json
import stat
from urllib.parse import parse_qs

import httpx
import pytest

from skdashboard import agent_bearer, auth_setup
from skdashboard.auth_setup import Config


@pytest.fixture
def cfg():
    return Config(hostname="node.example.ts.net", bind="100.64.0.9")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    adir = agent_bearer.agent_dir(tmp_path, "atlas")
    adir.mkdir(parents=True)
    (adir / "agent.json").write_text(json.dumps({"name": "atlas", "fingerprint": "A" * 40}))
    return adir


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_agent_names_are_constrained(tmp_path):
    with pytest.raises(ValueError):
        agent_bearer.agent_dir(tmp_path, "../etc")


def test_refresh_token_path_rotates_and_writes_private_bearer(tmp_path, cfg, agent):
    (agent / "refresh-token").write_text("old-refresh")
    seen = []

    def handler(request):
        form = parse_qs(request.content.decode())
        seen.append(form)
        assert request.url.path == "/oidc/token"
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["old-refresh"]
        return httpx.Response(200, json={"access_token": "bearer-1", "refresh_token": "new-refresh"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = agent_bearer.refresh(tmp_path, cfg, "atlas", "s3cret", http=client)
    assert path.read_text() == "bearer-1" and _mode(path) == 0o600
    assert (agent / "refresh-token").read_text() == "new-refresh"
    assert _mode(agent / "refresh-token") == 0o600
    assert seen[0]["client_secret"] == ["s3cret"]


def test_rejected_refresh_falls_back_to_signed_login(tmp_path, cfg, agent, monkeypatch):
    (agent / "refresh-token").write_text("expired")
    monkeypatch.setattr(agent_bearer, "_sign", lambda *a: "-----BEGIN PGP SIGNATURE-----")
    calls = []

    def handler(request):
        path = request.url.path
        calls.append(path)
        if path == "/oidc/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                return httpx.Response(401, json={"detail": "invalid_grant"})
            assert form["code"] == ["the-code"] and form["code_verifier"]
            return httpx.Response(200, json={"access_token": "bearer-2", "refresh_token": "r2"})
        if path == "/oidc/authorize":
            assert request.url.params["client_id"] == "skdashboard"
            assert request.url.params["code_challenge_method"] == "S256"
            return httpx.Response(200, text='const REQUEST_ID = "req-1";')
        if path == "/capauth/v1/challenge":
            return httpx.Response(
                200,
                json={
                    "nonce": "n",
                    "client_nonce_echo": "c",
                    "timestamp": "t",
                    "service": "s",
                    "expires": "e",
                },
            )
        if path == "/oidc/complete":
            body = json.loads(request.content)
            assert body["request_id"] == "req-1" and body["fingerprint"] == "A" * 40
            return httpx.Response(
                200, json={"redirect_to": f"{cfg.dashboard_url}/auth/callback?code=the-code&state=x"}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = agent_bearer.refresh(tmp_path, cfg, "atlas", "s3cret", http=client)
    assert path.read_text() == "bearer-2"
    assert calls == [
        "/oidc/token",
        "/oidc/authorize",
        "/capauth/v1/challenge",
        "/oidc/complete",
        "/oidc/token",
    ]


def test_refused_login_raises_without_writing_a_bearer(tmp_path, cfg, agent, monkeypatch):
    monkeypatch.setattr(agent_bearer, "_sign", lambda *a: "sig")

    def handler(request):
        if request.url.path == "/oidc/authorize":
            return httpx.Response(200, text='REQUEST_ID = "r";')
        if request.url.path == "/capauth/v1/challenge":
            return httpx.Response(
                200, json=dict(nonce="n", client_nonce_echo="c", timestamp="t", service="s", expires="e")
            )
        return httpx.Response(403, json={"detail": "fingerprint_not_approved"})

    with pytest.raises(RuntimeError):
        agent_bearer.refresh(tmp_path, cfg, "atlas", "s", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not agent_bearer.bearer_path("atlas").exists()


def test_bearer_timer_refreshes_well_inside_token_lifetime():
    units = auth_setup._bearer_units()
    assert "OnUnitActiveSec=4min" in units[auth_setup.BEARER_TIMER]
    assert "refresh-bearer %i" in units[auth_setup.BEARER_UNIT]


def test_agent_commands_parse(tmp_path):
    parser = auth_setup.build_parser()
    args = parser.parse_args(["agent-add", "atlas", "--key", "k.asc", "--passphrase-file", "p"])
    assert args.func is auth_setup.cmd_agent_add
    assert parser.parse_args(["grant", "--identity-class", "agent"]).identity_class == "agent"
