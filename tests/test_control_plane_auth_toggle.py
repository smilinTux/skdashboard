from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from skdashboard.control_plane_api import resolve_auth_mode
from skdashboard.dashboard import create_app

PATH = "/api/v1/board/summary?limit=1"


def _deny(_bearer: str, _capability: str, _target: str) -> bool:
    return False


def _client(home: Path, mode: str, host: str = "127.0.0.1") -> TestClient:
    app = create_app(home, control_plane_authorizer=_deny, auth_mode=mode)
    return TestClient(app, client=(host, 50000), base_url="http://localhost:7778")


def test_off_waives_bearer_for_direct_loopback(tmp_path):
    assert _client(tmp_path, "off").get(PATH).status_code == 200


def test_off_accepts_ipv6_loopback_and_local_origin(tmp_path):
    client = _client(tmp_path, "off", host="::1")
    response = client.get(PATH, headers={"Origin": "http://localhost:7778"})
    assert response.status_code == 200


def test_off_still_challenges_remote_clients(tmp_path):
    assert _client(tmp_path, "off", host="192.168.0.41").get(PATH).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example:7778"},
        {"X-Forwarded-For": "192.168.0.41"},
        {"Forwarded": "for=192.168.0.41"},
        {"Origin": "https://evil.example"},
    ],
)
def test_off_refuses_rebinding_proxied_and_foreign_origin(tmp_path, headers):
    assert _client(tmp_path, "off").get(PATH, headers=headers).status_code in {401, 403}


def test_on_requires_bearer_even_from_loopback(tmp_path):
    assert _client(tmp_path, "on").get(PATH).status_code == 401


def test_routes_default_is_on(tmp_path):
    from skdashboard.control_plane_api import routes

    with pytest.raises(ValueError):
        routes(tmp_path, board_reader=None, health_reader=None, auth_mode="maybe")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "off"), ("", "off"), ("off", "off"), ("OFF", "off"), ("on", "on"), ("of", "on")],
)
def test_resolve_auth_mode_fails_closed_on_typos(raw, expected):
    env = {} if raw is None else {"SKDASHBOARD_AUTH": raw}
    assert resolve_auth_mode(env) == expected
