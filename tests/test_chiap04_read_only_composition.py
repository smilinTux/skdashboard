from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from starlette.requests import Request

from skdashboard.control_plane_api import ALLOWED_BROWSER_ORIGINS
from skdashboard.live_control_plane import (
    CAPABILITY,
    TARGET,
    LiveControlPlaneConfig,
    compose_live_control_plane,
)
from skdashboard.runtime_boundary import load_runtime_boundary

LAN_ORIGIN = "https://10.0.0.101:7780"
TAILNET_ORIGIN = "https://100.84.237.127:7780"


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

    invalid = (
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS="http://10.0.0.101:7780"),
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS=f"{LAN_ORIGIN}/path"),
        _environment(SKDASHBOARD_ALLOWED_BROWSER_ORIGINS="https://10.0.0.55:7780"),
        _environment(SKDASHBOARD_ALLOWED_BIND_HOSTS="127.0.0.1,10.0.0.101,10.0.0.101"),
    )
    for environ in invalid:
        with pytest.raises(ValueError):
            load_runtime_boundary(environ)


def test_chiap04_node_is_bound_into_authorization_invocation() -> None:
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
    origin = sorted(ALLOWED_BROWSER_ORIGINS)[0]
    parsed = urlsplit(origin)
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
                (b"origin", origin.encode()),
                (b"x-request-id", b"chiap04-composition-check"),
            ],
        }
    )
    invocation = composition.invocation_factory(request, CAPABILITY, TARGET)
    assert invocation.node_id == "chiap04"
    assert invocation.boundary.origin == origin


def test_chiap04_unit_is_dedicated_and_never_owns_legacy_ports() -> None:
    unit = (
        Path(__file__).parents[1] / "deploy/systemd/skdashboard-read-only-chiap04@.service"
    ).read_text(encoding="utf-8")
    assert "SKDASHBOARD_ALLOWED_BIND_HOSTS=127.0.0.1,10.0.0.101,100.84.237.127" in unit
    assert (
        "SKDASHBOARD_ALLOWED_BROWSER_ORIGINS=https://10.0.0.101:7780,https://100.84.237.127:7780"
    ) in unit
    assert "EnvironmentFile=%h/.config/skdashboard-chiap04/runtime.env" in unit
    assert "skdashboard-readonly-chiap04/approved-venv" in unit
    assert "--host %i --port 7780" in unit
    assert "--session-db %h/.local/state/skdashboard-chiap04/session-%i.db" in unit
    assert (
        "--operator-session-db %h/.local/state/skdashboard-chiap04/operator-session-%i.db" in unit
    )
    assert "--node-id chiap04" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "NoNewPrivileges=true" in unit
    assert "skcapstone-dashboard.service" not in unit
    assert "--port 7778" not in unit
    assert "--port 8765" not in unit
