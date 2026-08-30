from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from skdashboard.dashboard import create_app
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


def _full_client(home: Path) -> TestClient:
    return TestClient(create_app(home))


def test_build_info_uses_bounded_runtime_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("skdashboard.build_info.metadata.version", lambda _name: "0.1.90")
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


@pytest.mark.parametrize("client_factory", [_client, _full_client])
def test_build_info_fails_honestly_without_safe_metadata(
    tmp_path: Path, monkeypatch, client_factory
) -> None:
    monkeypatch.setattr("skdashboard.build_info.metadata.version", lambda _name: "0.1.90")
    monkeypatch.delenv("SKDASHBOARD_SOURCE_COMMIT", raising=False)
    monkeypatch.setenv("SKDASHBOARD_RELEASE_IDENTIFIER", "/secret/release/path")

    result = client_factory(tmp_path).get("/api/v1/build-info").json()

    assert result["source_commit"] == "unavailable"
    assert result["release_identifier"] == "unavailable"
    assert "/" not in result["source_commit"]
    assert "/" not in result["release_identifier"]


def test_full_runtime_reuses_bounded_build_info(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("skdashboard.build_info.metadata.version", lambda _name: "0.1.91")
    monkeypatch.setenv("SKDASHBOARD_SOURCE_COMMIT", "91B248A630591117B65FE0A5C211E30DE1A09211")
    monkeypatch.setenv("SKDASHBOARD_RELEASE_IDENTIFIER", "v0.1.91")

    response = _full_client(tmp_path).get("/api/v1/build-info")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": "skdashboard.build-info/v1",
        "application": "SKDashboard",
        "package_version": "0.1.91",
        "source_commit": "91b248a63059",
        "release_identifier": "v0.1.91",
    }


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


def test_every_full_runtime_surface_loads_shared_badge_module(tmp_path: Path) -> None:
    client = _full_client(tmp_path)

    shared_api = client.get("/static/js/api.js")
    assert shared_api.status_code == 200
    assert shared_api.text.count('import "./read_only_api.js";') == 1
    for route, asset in SURFACES.items():
        assert client.get(f"/control-plane/{route}").status_code == 200
        script = client.get(f"/static/js/{asset}.js")
        assert script.status_code == 200
        assert script.text.count('from "./api.js"') == 1


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


def test_full_runtime_shared_api_renders_exactly_one_badge() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable")
    module = Path("src/skdashboard/static/js/api.js").resolve().as_uri()
    program = """
const inserted = [];
const navigation = {
  querySelector() { return null; },
  append(value) { inserted.push(value); },
};
globalThis.document = {
  querySelector(selector) { return selector === ".topbar, .sidebar" ? navigation : null; },
  createElement() {
    return {
      setAttribute(name, value) { this[name] = value; },
      textContent: "",
    };
  },
};
globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({
    schema_version: "skdashboard.build-info/v1",
    application: "SKDashboard",
    package_version: "0.1.91",
    source_commit: "91b248a63059",
    release_identifier: "v0.1.91",
  }),
});
await import(process.argv[1]);
await new Promise((resolve) => setTimeout(resolve, 0));
if (inserted.length !== 1) process.exit(1);
if (inserted[0].id !== "build-version") process.exit(1);
if (inserted[0].textContent !== "SKDashboard 0.1.91 | 91b248a63059 | v0.1.91") process.exit(1);
"""
    subprocess.run(
        [node, "--input-type=module", "--eval", program, module],
        check=True,
        capture_output=True,
        text=True,
    )
