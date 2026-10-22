"""Protected Economy entrypoint and browser behavior over the shared contract."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from skdashboard.live_control_plane import AUTHENTICATED_BINDINGS
from skdashboard.read_only import create_read_only_app

ROOT = Path(__file__).parents[1]
ORIGIN = "https://10.0.0.139:7778"


def test_safe_economy_uses_only_protected_assets_and_queries(tmp_path):
    client = TestClient(
        create_read_only_app(tmp_path, authorizer=lambda bearer, *_: bearer == "reader"),
        base_url=ORIGIN,
    )
    page = client.get("/economy")
    assert page.status_code == 200
    assert "Gateway Economy" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert client.post("/economy").status_code == 405
    for asset in ("js/gateway_client.js", "js/gateway_economy.js", "css/gateway_economy.css"):
        assert client.get(f"/static/{asset}").status_code == 200
    assert client.get("/api/economy").status_code == 404
    assert client.get("/static/js/economy.js").status_code == 404
    target = "/api/v1/gateway/timeseries"
    assert ("skdashboard.read", target) in AUTHENTICATED_BINDINGS
    assert client.get(target).status_code == 401
    assert client.get(target, headers={"Authorization": "Bearer invalid"}).status_code == 403
    assert client.get(target, headers={"Authorization": "Bearer reader"}).status_code == 503


def test_economy_link_remains_on_safe_runtime(tmp_path):
    client = TestClient(
        create_read_only_app(tmp_path, legacy_board_url="https://legacy.example/board"),
        base_url=ORIGIN,
    )
    page = client.get("/control-plane/ai")
    assert 'href="/economy"' in page.text
    assert 'href="https://legacy.example/economy"' not in page.text


def test_gateway_economy_browser_contract_accessibility_and_denial_purge():
    if not shutil.which("node") or not shutil.which("google-chrome"):
        pytest.skip("Node and Chrome are required for browser qualification")
    result = subprocess.run(
        ["node", "scripts/qualify_gateway_economy_cdp.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["result"] == "PASS"
    assert evidence["states"] == ["current", "stale", "partial", "empty", "401", "403", "429", "503"]
    assert evidence["responsive"] == [320, 390]
    assert evidence["staleResponseBlocked"]
    assert evidence["noLegacyRequests"]
