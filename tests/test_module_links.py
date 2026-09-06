"""Coverage for the read-only dashboard module destinations and navigation."""
from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from skdashboard.dashboard import create_app

ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src" / "skdashboard" / "static"


def test_module_routes_are_explicit_and_read_only(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    for path in ("/economy", "/fleet", "/drift", "/control-plane/portfolio"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Source:" in response.text or "Portfolio" in response.text
        assert client.post(path).status_code == 405


def test_sidebar_links_cover_economy_fleet_drift_and_portfolio() -> None:
    pages = list(STATIC.glob("*.html"))
    assert pages
    for page in pages:
        html = page.read_text(encoding="utf-8")
        if 'class="topbar sidebar"' not in html and 'class="topbar sidebar"' not in html.replace("\n", " "):
            continue
        assert 'data-nav="portfolio"' in html, page.name
        assert 'data-nav="economy"' in html, page.name
        assert 'data-nav="fleet"' in html, page.name
        assert 'data-nav="drift"' in html, page.name
        assert 'href="/economy"' in html, page.name
        assert 'href="/fleet"' in html, page.name
        assert 'href="/drift"' in html, page.name
        assert 'href="/control-plane/portfolio' in html, page.name
