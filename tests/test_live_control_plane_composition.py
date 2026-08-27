import asyncio
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from skdashboard.control_plane_api import ALLOWED_BROWSER_ORIGINS
from skdashboard.live_control_plane import (
    CAPABILITY,
    RESOURCE_TYPE,
    TARGET,
    EphemeralIssuerClient,
    LiveControlPlaneConfig,
    compose_live_control_plane,
)
from skdashboard.read_only import create_read_only_app

ORIGIN = sorted(ALLOWED_BROWSER_ORIGINS)[0]
RESOURCE_ID = "authorized-card-set:sha256:" + "a" * 64
POLICY_REVISION = "b" * 64


def config(tmp_path: Path, *, board="https://legacy.example/board") -> LiveControlPlaneConfig:
    return LiveControlPlaneConfig(
        issuer_socket=tmp_path / "issuer.sock",
        legacy_board_url=board,
        resource_id=RESOURCE_ID,
        owner_policy_revision=POLICY_REVISION,
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

    assert composition.decision_authorizer._owner_policy is composition.project_provider
    invocation = composition.invocation_factory(request(), CAPABILITY, TARGET)
    assert invocation.node_id == "chiap04"
    assert invocation.purpose == "project-management-reporting"
    assert invocation.audience == "skdashboard"
    assert invocation.capability == "skdashboard.read"
    assert invocation.target == "/api/v1/overview"
    assert invocation.resource_type == RESOURCE_TYPE
    assert invocation.resource_id == RESOURCE_ID
    assert invocation.boundary.origin == ORIGIN


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


def test_issuer_opens_a_fresh_channel_for_every_request_and_sends_exact_facts(tmp_path) -> None:
    cfg = config(tmp_path)
    calls = []

    async def scenario():
        async def serve(reader, writer):
            line = await reader.readline()
            calls.append(json.loads(line))
            writer.write(
                json.dumps(
                    {
                        "schema_version": "skdashboard-issuer-response/v1",
                        "bearer": f"fresh-{len(calls)}",
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(serve, path=cfg.issuer_socket)
        os.chmod(cfg.issuer_socket, 0o660)
        try:
            issuer = EphemeralIssuerClient(cfg)
            session = SimpleNamespace(access_token="server-session-credential")
            first = await issuer(request(request_id="one"), session, CAPABILITY, TARGET)
            second = await issuer(request(request_id="two"), session, CAPABILITY, TARGET)
            return first, second
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(scenario()) == ("fresh-1", "fresh-2")
    assert len(calls) == 2
    assert calls[0]["request_id"] == "one" and calls[1]["request_id"] == "two"
    for payload in calls:
        assert payload["audience"] == "skdashboard"
        assert payload["capability"] == "skdashboard.read"
        assert payload["target"] == TARGET
        assert payload["resource_type"] == RESOURCE_TYPE
        assert payload["resource_id"] == RESOURCE_ID
        assert payload["owner_policy_revision"] == POLICY_REVISION
        assert payload["ttl_seconds"] == 60
        assert payload["use_limit"] == 1
        assert payload["store"] is False


def test_issuer_fails_closed_before_connecting_for_wrong_binding(tmp_path, monkeypatch) -> None:
    cfg = config(tmp_path)
    raw = socket.socket(socket.AF_UNIX)
    raw.bind(str(cfg.issuer_socket))
    raw.close()
    os.chmod(cfg.issuer_socket, 0o666)
    connected = Mock()
    monkeypatch.setattr(asyncio, "open_unix_connection", connected)

    with pytest.raises(PermissionError):
        asyncio.run(
            EphemeralIssuerClient(cfg)(
                request(), SimpleNamespace(access_token="session"), CAPABILITY, TARGET
            )
        )
    connected.assert_not_called()


def test_read_only_runtime_serves_now_portfolio_static_and_external_board(tmp_path) -> None:
    board = "https://legacy.example/explicit-board"
    app = create_read_only_app(tmp_path, legacy_board_url=board)
    client = TestClient(app, base_url=ORIGIN)

    now = client.get("/control-plane/now")
    portfolio = client.get("/control-plane/portfolio")
    css = client.get("/static/css/overview.css")
    javascript = client.get("/static/js/overview.js")
    assert now.status_code == portfolio.status_code == css.status_code == 200
    assert "<h2>Now</h2>" in now.text
    assert "Portfolio" in portfolio.text
    assert f'href="{board}"' in now.text
    assert f'href="{board}"' in portfolio.text
    assert 'href="/board"' not in now.text + portfolio.text
    assert board in javascript.text
    assert 'href="/board"' not in javascript.text
    assert client.get("/board").status_code == 404
    assert client.post("/api/card/example/mutate").status_code == 404


def test_legacy_board_configuration_rejects_urls_with_credentials_or_query(tmp_path) -> None:
    for value in (
        "http://legacy.example/board",
        "https://user:secret@legacy.example/board",
        "https://legacy.example/board?token=secret",
    ):
        with pytest.raises(ValueError):
            config(tmp_path, board=value)
