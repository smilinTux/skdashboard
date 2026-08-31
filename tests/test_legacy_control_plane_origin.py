from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from skdashboard.dashboard import create_app, start_dashboard

CANONICAL_ORIGIN = "https://control.example.test:8443"


def _client(home: Path, origin: str | None = CANONICAL_ORIGIN) -> TestClient:
    return TestClient(
        create_app(home, canonical_control_plane_origin=origin),
        follow_redirects=False,
    )


def test_configured_origin_redirects_get_and_head_with_safe_scope(tmp_path: Path) -> None:
    path = "/control-plane/reports%20latest"
    query = "role=operator&scope=estate&window=latest&selected_silo=legal"
    expected = f"{CANONICAL_ORIGIN}{path}?{query}"
    client = _client(tmp_path)

    get_response = client.get(f"{path}?{query}")
    head_response = client.head(f"{path}?{query}")

    assert get_response.status_code == 307
    assert get_response.headers["location"] == expected
    assert get_response.headers["cache-control"] == "no-store"
    assert head_response.status_code == 307
    assert head_response.headers["location"] == expected
    assert head_response.content == b""


def test_redirect_is_get_head_only_and_api_auth_stays_local(tmp_path: Path) -> None:
    client = _client(tmp_path)

    post_response = client.post("/control-plane/now")
    api_response = client.get("/api/v1/overview")

    assert post_response.status_code == 405
    assert "location" not in post_response.headers
    assert api_response.status_code == 401
    assert api_response.json()["code"] == "UNAUTHORIZED"
    assert "location" not in api_response.headers


def test_absent_origin_preserves_local_control_plane(tmp_path: Path) -> None:
    response = _client(tmp_path, origin=None).get("/control-plane/now")

    assert response.status_code == 200
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "",
        " https://control.example.test",
        "http://control.example.test",
        "https://user@control.example.test",
        "https://user:password@control.example.test",
        "https://control.example.test/path",
        "https://control.example.test?scope=estate",
        "https://control.example.test#fragment",
        "https://control.example.test\\@redirect.example",
        "https://control.example.test%2f@redirect.example",
        "https://control.example.test:99999",
    ],
)
def test_invalid_origin_fails_app_construction(tmp_path: Path, origin: str) -> None:
    with pytest.raises(ValueError, match="exact HTTPS origin"):
        create_app(tmp_path, canonical_control_plane_origin=origin)


@pytest.mark.parametrize(
    "query",
    [
        "token=secret",
        "scope=estate&scope=project",
        "scope=",
        "scope=%ZZ",
        f"selected_silo={'x' * 129}",
        f"scope={'x' * 2049}",
    ],
)
def test_unsafe_or_unbounded_query_does_not_redirect(tmp_path: Path, query: str) -> None:
    response = _client(tmp_path).get(f"/control-plane/now?{query}")

    assert response.status_code == 400
    assert "location" not in response.headers


def test_start_dashboard_reads_optional_origin_without_changing_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKDASHBOARD_CANONICAL_CONTROL_PLANE_ORIGIN", CANONICAL_ORIGIN)
    with patch("skdashboard.dashboard.create_app", return_value=object()) as build:
        server = start_dashboard(tmp_path)

    build.assert_called_once_with(tmp_path, canonical_control_plane_origin=CANONICAL_ORIGIN)
    assert server._server.config.host == "127.0.0.1"


def test_invalid_environment_origin_fails_before_server_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKDASHBOARD_CANONICAL_CONTROL_PLANE_ORIGIN", "http://unsafe.example")

    with pytest.raises(ValueError, match="exact HTTPS origin"):
        start_dashboard(tmp_path)
