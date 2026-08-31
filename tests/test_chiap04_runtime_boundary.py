from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from starlette.requests import Request

from skdashboard.live_control_plane import (
    CAPABILITY,
    TARGET,
    LiveControlPlaneConfig,
    compose_live_control_plane,
)
from skdashboard.runtime_boundary import load_runtime_boundary

LAN_ORIGIN = "https://10.0.0.101:7780"
TAILNET_ORIGIN = "https://100.84.237.127:7780"
TAILNET_FQDN_ORIGIN = "https://chiap04.tail204f0c.ts.net:7780"


def _environment(**values):
    return {
        "SKDASHBOARD_ALLOWED_BIND_HOSTS": "127.0.0.1,10.0.0.101,100.84.237.127",
        "SKDASHBOARD_ALLOWED_BROWSER_ORIGINS": f"{LAN_ORIGIN},{TAILNET_ORIGIN}",
        **values,
    }


def test_chiap04_process_boundary_is_exact_and_fail_closed() -> None:
    origins, hosts = load_runtime_boundary(_environment())
    assert origins == {LAN_ORIGIN, TAILNET_ORIGIN}
    assert hosts == {"127.0.0.1", "10.0.0.101", "100.84.237.127"}

    for environ in (
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS="http://10.0.0.101:7780"),
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS=f"{LAN_ORIGIN}/path"),
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS="https://10.0.0.55:7780"),
    ):
        with pytest.raises(ValueError):
            load_runtime_boundary(environ)


def test_browser_origin_may_use_an_exact_fqdn_without_widening_bind_hosts() -> None:
    origins, hosts = load_runtime_boundary(
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS=f"{LAN_ORIGIN},{TAILNET_FQDN_ORIGIN}")
    )
    assert origins == {LAN_ORIGIN, TAILNET_FQDN_ORIGIN}
    assert hosts == {"127.0.0.1", "10.0.0.101", "100.84.237.127"}

    with pytest.raises(ValueError):
        load_runtime_boundary(
            _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS="https://localhost:7780")
        )


def test_chiap04_node_is_bound_into_authorization_invocation(monkeypatch) -> None:
    monkeypatch.setattr(
        "skdashboard.live_control_plane.ALLOWED_BROWSER_ORIGINS", {LAN_ORIGIN, TAILNET_ORIGIN}
    )
    config = LiveControlPlaneConfig(
        legacy_board_url="https://legacy.example/board",
        resource_id="authorized-card-set:sha256:" + "a" * 64,
        owner_policy_revision="b" * 64,
        tenant_id="platform",
        node_id="chiap04",
        capability_ttl_seconds=60,
    )
    composition = compose_live_control_plane(
        config=config,
        capability_authorizer=Mock(),
        owner_policy_backend=Mock(),
        store_factory=Mock(),
    )
    parsed = urlsplit(LAN_ORIGIN)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": (parsed.hostname, parsed.port),
            "client": ("127.0.0.1", 1),
            "path": TARGET,
            "raw_path": TARGET.encode(),
            "query_string": b"",
            "headers": [
                (b"host", parsed.netloc.encode()),
                (b"origin", LAN_ORIGIN.encode()),
                (b"x-request-id", b"chiap04-composition-check"),
            ],
        }
    )

    invocation = composition.invocation_factory(request, CAPABILITY, TARGET)
    assert invocation.node_id == "chiap04"
    assert invocation.boundary.origin == LAN_ORIGIN
