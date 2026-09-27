from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from skdashboard import auth_setup
from skdashboard.auth_setup import Config, Layout, render_units


@pytest.fixture
def cfg():
    return Config(hostname="node.example.ts.net", bind="100.64.0.9", agent_home="/h/.skcapstone")


def test_units_pin_every_capauth_path_inside_the_state_dir(tmp_path, cfg):
    layout = Layout(tmp_path)
    idp = render_units(layout, cfg)[auth_setup.IDP_UNIT]
    for key in (
        "CAPAUTH_HOME",
        "CAPAUTH_DB_PATH",
        "CAPAUTH_DATA_DIR",
        "CAPAUTH_OIDC_STATE_DB",
        "CAPAUTH_OIDC_SIGNING_KEY_PATH",
        "CAPAUTH_PASSKEY_DATA_DIR",
        "GNUPGHOME",
    ):
        line = next(item for item in idp.splitlines() if item.startswith(f"Environment={key}="))
        assert str(tmp_path) in line, key
    assert "CAPAUTH_OIDC_ISSUER=https://node.example.ts.net:8421" in idp
    assert "CAPAUTH_REQUIRE_APPROVAL=true" in idp
    assert "--host 100.64.0.9 --port 8421" in idp


def test_dashboard_unit_verifies_with_public_keyring_and_node_local_grants(tmp_path, cfg):
    layout = Layout(tmp_path)
    unit = render_units(layout, cfg)[auth_setup.DASHBOARD_UNIT]
    assert f"GNUPGHOME={layout.verify_gnupg}" in unit
    assert f"--capauth-home {layout.capauth_home}" in unit
    assert "--home /h/.skcapstone" in unit
    assert "SKDASHBOARD_ALLOWED_BROWSER_ORIGINS=https://node.example.ts.net:7779" in unit
    assert "--oidc-redirect-uri https://node.example.ts.net:7779/auth/callback" in unit


def test_toggle_dropin_records_mode_and_secure_url(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(auth_setup, "UNIT_DIR", tmp_path)
    on = auth_setup.set_local_auth("on", cfg).read_text()
    assert "Environment=SKDASHBOARD_AUTH=on" in on
    assert "Environment=SKDASHBOARD_SECURE_URL=https://node.example.ts.net:7779" in on
    off = auth_setup.set_local_auth("off", None).read_text()
    assert "SKDASHBOARD_AUTH=off" in off and "SECURE_URL" not in off


def test_local_login_hands_off_to_secure_dashboard(tmp_path, monkeypatch):
    from skdashboard.dashboard import create_app

    monkeypatch.setenv("SKDASHBOARD_SECURE_URL", "https://node.example.ts.net:7779")
    client = TestClient(create_app(tmp_path, auth_mode="on"))
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://node.example.ts.net:7779/control-plane/now"


def test_local_login_route_absent_without_secure_url(tmp_path, monkeypatch):
    from skdashboard.dashboard import create_app

    monkeypatch.delenv("SKDASHBOARD_SECURE_URL", raising=False)
    client = TestClient(create_app(tmp_path, auth_mode="on"))
    assert client.get("/auth/login", follow_redirects=False).status_code == 404


def test_grant_and_revoke_parse(tmp_path):
    parser = auth_setup.build_parser()
    args = parser.parse_args(["--state-dir", str(tmp_path), "grant", "--pubkey", "k.asc"])
    assert args.func is auth_setup.cmd_grant
    args = parser.parse_args(["revoke", "AB" * 20])
    assert args.fingerprint == "AB" * 20
