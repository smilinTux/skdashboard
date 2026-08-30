from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from skdashboard.read_only import create_read_only_app

ORIGIN = "https://10.0.0.139:7778"
SURFACES = {
    "now": "overview",
    "portfolio": "projects",
    "schedule": "schedule",
    "reliability": "reliability",
    "architecture": "architecture",
    "ai": "ai",
    "governance": "governance",
    "reports": "reports",
}


def _client(home: Path) -> TestClient:
    return TestClient(create_read_only_app(home), base_url=ORIGIN)


def test_build_info_uses_bounded_runtime_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("skdashboard.read_only.metadata.version", lambda _name: "0.1.90")
    monkeypatch.setenv("SKDASHBOARD_SOURCE_COMMIT", "ABCDEF0123456789ABCDEF")
    monkeypatch.setenv("SKDASHBOARD_RELEASE_IDENTIFIER", "v0.1.90")

    response = _client(tmp_path).get("/api/v1/build-info")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": "skdashboard.build-info/v1",
        "application": "SKDashboard",
        "package_version": "0.1.90",
        "source_commit": "abcdef012345",
        "release_identifier": "v0.1.90",
    }


def test_build_info_fails_honestly_without_safe_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("skdashboard.read_only.metadata.version", lambda _name: "0.1.90")
    monkeypatch.delenv("SKDASHBOARD_SOURCE_COMMIT", raising=False)
    monkeypatch.setenv("SKDASHBOARD_RELEASE_IDENTIFIER", "/secret/release/path")

    result = _client(tmp_path).get("/api/v1/build-info").json()

    assert result["source_commit"] == "unavailable"
    assert result["release_identifier"] == "unavailable"
    assert "/" not in result["source_commit"]
    assert "/" not in result["release_identifier"]


def test_every_current_surface_loads_one_runtime_badge_seam(tmp_path: Path) -> None:
    client = _client(tmp_path)
    badge_script = client.get("/static/js/read_only_api.js")

    assert badge_script.status_code == 200
    assert 'getJSON("/api/v1/build-info")' in badge_script.text
    assert 'badge.textContent = "Version unavailable"' in badge_script.text
    assert "0.1.90" not in badge_script.text
    for route, asset in SURFACES.items():
        assert client.get(f"/control-plane/{route}").status_code == 200
        script = client.get(f"/static/js/{asset}.js")
        assert 'from "./read_only_api.js"' in script.text


def test_badge_attaches_with_and_without_live_anchor() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable")
    module = Path("src/skdashboard/static/js/read_only_api.js").resolve().as_uri()
    program = """
const { attachBuildBadge } = await import(process.argv[1]);
for (const hasLive of [true, false]) {
  const calls = [];
  const badge = {};
  const live = hasLive ? { before(value) { calls.push(["before", value]); } } : null;
  const navigation = {
    querySelector(selector) { return selector === ".live" ? live : null; },
    append(value) { calls.push(["append", value]); },
  };
  attachBuildBadge(navigation, badge);
  const expected = hasLive ? "before" : "append";
  if (calls.length !== 1 || calls[0][0] !== expected || calls[0][1] !== badge) process.exit(1);
}
"""
    subprocess.run(
        [node, "--input-type=module", "--eval", program, module],
        check=True,
        capture_output=True,
        text=True,
    )
