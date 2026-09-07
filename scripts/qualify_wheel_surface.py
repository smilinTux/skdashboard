"""Fail a release whose built wheel omits the supported dashboard surface."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED_FILES = {
    "skdashboard/dashboard_observability.py",
    "skdashboard/dashboard_fleet.py",
    "skdashboard/dashboard_economy.py",
    "skdashboard/assistant_client.py",
    "skdashboard/static/js/fleet.js",
    "skdashboard/static/js/economy.js",
    "skdashboard/static/js/observability.js",
    "skdashboard/static/js/assistant.js",
}
REQUIRED_ROUTES = {
    "/control-plane/fleet",
    "/control-plane/reports",
    "/board",
    "/cmdb",
    "/economy",
    "/fleet",
    "/observability",
    "/assistant",
    "/fleet-chat",
    "/api/v1/fleet/drift",
    "/api/v1/economy/summary",
    "/api/v1/observability",
    "/api/assistant",
}


def qualify(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing_files = sorted(REQUIRED_FILES - names)
        route_sources = "\n".join(
            archive.read(name).decode("utf-8")
            for name in names
            if name.startswith("skdashboard/") and name.endswith(".py")
        )
    missing_routes = sorted(route for route in REQUIRED_ROUTES if route not in route_sources)
    if missing_files or missing_routes:
        raise SystemExit(
            f"incomplete wheel: missing files={missing_files}, missing routes={missing_routes}"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: qualify_wheel_surface.py DIST.whl")
    qualify(Path(sys.argv[1]))
