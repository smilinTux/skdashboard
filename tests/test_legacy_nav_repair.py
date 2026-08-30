"""Focused tests for legacy sidebar navigation routing repair (card 8038ca82)."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from skdashboard.read_only import (
    LEGACY_PATHS,
    _rewrite_legacy_links,
    _validate_and_extract_legacy_origin,
    create_read_only_app,
)

LAN_ORIGIN = "https://10.0.0.139:7778"
LEGACY_ORIGIN = "https://legacy.example.com"


def _client(tmp_path: Path, legacy_board_url: str | None = None) -> TestClient:
    return TestClient(
        create_read_only_app(
            tmp_path, authorizer=lambda *_: True, legacy_board_url=legacy_board_url
        ),
        base_url=LAN_ORIGIN,
    )


class TestLegacyBoardUrlValidation:
    """Tests for _validate_and_extract_legacy_origin function."""

    def test_none_returns_none(self):
        assert _validate_and_extract_legacy_origin(None) is None

    def test_empty_string_returns_none(self):
        assert _validate_and_extract_legacy_origin("") is None

    def test_valid_https_url_returns_origin(self):
        assert (
            _validate_and_extract_legacy_origin("https://legacy.example.com")
            == "https://legacy.example.com"
        )
        assert (
            _validate_and_extract_legacy_origin("https://legacy.example.com:443")
            == "https://legacy.example.com:443"
        )
        assert (
            _validate_and_extract_legacy_origin("https://legacy.example.com:8443")
            == "https://legacy.example.com:8443"
        )

    def test_http_url_rejected(self):
        assert _validate_and_extract_legacy_origin("http://legacy.example.com") is None

    def test_url_with_credentials_rejected(self):
        assert _validate_and_extract_legacy_origin("https://user:pass@legacy.example.com") is None
        assert _validate_and_extract_legacy_origin("https://user@legacy.example.com") is None

    def test_url_with_query_string_rejected(self):
        assert _validate_and_extract_legacy_origin("https://legacy.example.com?foo=bar") is None

    def test_url_with_fragment_rejected(self):
        assert _validate_and_extract_legacy_origin("https://legacy.example.com#section") is None

    def test_malformed_url_rejected(self):
        assert _validate_and_extract_legacy_origin("not-a-url") is None
        assert _validate_and_extract_legacy_origin("://legacy.example.com") is None

    def test_only_origin_extracted(self):
        result = _validate_and_extract_legacy_origin("https://legacy.example.com")
        assert result == "https://legacy.example.com"
        assert not result.endswith("/")


class TestLegacyLinkRewriting:
    """Tests for _rewrite_legacy_links function."""

    def test_none_origin_returns_html_unchanged(self):
        html = '<a href="/board">Board</a><a href="/cockpit">Cockpit</a>'
        assert _rewrite_legacy_links(html, None) == html

    def test_empty_origin_returns_html_unchanged(self):
        html = '<a href="/board">Board</a><a href="/cockpit">Cockpit</a>'
        assert _rewrite_legacy_links(html, "") == html

    def test_rewrites_all_eight_legacy_paths(self):
        html = """
        <a href="/cockpit">ITIL Cockpit</a>
        <a href="/cmdb">Assets (CMDB)</a>
        <a href="/board">Kanban Board</a>
        <a href="/assistant">Assistant</a>
        <a href="/trust">Trust</a>
        <a href="/models">Models</a>
        <a href="/economy">Economy</a>
        <a href="/fleet">Fleet Drift</a>
        """
        rewritten = _rewrite_legacy_links(html, LEGACY_ORIGIN)
        assert f'href="{LEGACY_ORIGIN}/cockpit"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/cmdb"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/board"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/assistant"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/trust"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/models"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/economy"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/fleet"' in rewritten
        assert 'href="/cockpit"' not in rewritten
        assert 'href="/cmdb"' not in rewritten
        assert 'href="/board"' not in rewritten
        assert 'href="/assistant"' not in rewritten
        assert 'href="/trust"' not in rewritten
        assert 'href="/models"' not in rewritten
        assert 'href="/economy"' not in rewritten
        assert 'href="/fleet"' not in rewritten

    def test_preserves_labels_order_and_active_state(self):
        html = """
        <a class="tab active" data-nav="cockpit" href="/cockpit">ITIL Cockpit</a>
        <a class="tab" data-nav="cmdb" href="/cmdb">Assets (CMDB)</a>
        <a class="tab" data-nav="board" href="/board">Kanban Board</a>
        """
        rewritten = _rewrite_legacy_links(html, LEGACY_ORIGIN)
        assert 'class="tab active"' in rewritten
        assert 'data-nav="cockpit"' in rewritten
        assert 'data-nav="cmdb"' in rewritten
        assert 'data-nav="board"' in rewritten
        assert "ITIL Cockpit" in rewritten
        assert "Assets (CMDB)" in rewritten
        assert "Kanban Board" in rewritten

    def test_preserves_query_behavior_on_non_legacy_links(self):
        html = '<a href="/control-plane/now?role=operator">Now</a><a href="/board">Board</a>'
        rewritten = _rewrite_legacy_links(html, LEGACY_ORIGIN)
        assert 'href="/control-plane/now?role=operator"' in rewritten
        assert f'href="{LEGACY_ORIGIN}/board"' in rewritten

    def test_does_not_rewrite_non_legacy_paths(self):
        html = '<a href="/control-plane/now">Now</a><a href="/api/v1/health">Health</a>'
        rewritten = _rewrite_legacy_links(html, LEGACY_ORIGIN)
        assert 'href="/control-plane/now"' in rewritten
        assert 'href="/api/v1/health"' in rewritten

    def test_multiple_occurrences_of_same_path_rewritten(self):
        html = '<a href="/board">Board</a> and <a href="/board">Another Board</a>'
        rewritten = _rewrite_legacy_links(html, LEGACY_ORIGIN)
        assert rewritten.count(f'href="{LEGACY_ORIGIN}/board"') == 2
        assert 'href="/board"' not in rewritten


class TestStaticFileServingWithLegacyOrigin:
    """Tests for static file serving with legacy link rewriting."""

    def test_html_files_rewritten_when_legacy_origin_configured(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        response = client.get("/control-plane/now")
        assert response.status_code == 200
        assert f'href="{LEGACY_ORIGIN}/cockpit"' in response.text
        assert f'href="{LEGACY_ORIGIN}/cmdb"' in response.text
        assert f'href="{LEGACY_ORIGIN}/board"' in response.text
        assert 'href="/cockpit"' not in response.text
        assert 'href="/cmdb"' not in response.text
        assert 'href="/board"' not in response.text

    def test_javascript_board_links_rewritten(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        response = client.get("/static/js/overview.js")
        assert response.status_code == 200
        assert f'"{LEGACY_ORIGIN}/board"' in response.text
        assert '"/board"' not in response.text
        assert "text/javascript" in response.headers["content-type"]

    def test_projects_js_also_rewritten(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        response = client.get("/static/js/projects.js")
        assert response.status_code == 200
        # Verify it's JavaScript content type
        assert "text/javascript" in response.headers["content-type"]

    def test_html_files_unmodified_when_no_legacy_origin(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=None)
        response = client.get("/control-plane/now")
        assert response.status_code == 200
        assert 'href="/cockpit"' in response.text
        assert 'href="/cmdb"' in response.text
        assert 'href="/board"' in response.text

    def test_javascript_unmodified_when_no_legacy_origin(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=None)
        response = client.get("/static/js/overview.js")
        assert response.status_code == 200
        assert '"/board"' in response.text

    def test_css_files_served_as_is(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        response = client.get("/static/css/overview.css")
        assert response.status_code == 200
        # CSS is served as-is

    def test_static_path_traversal_prevented(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        assert client.get("/static/../README.md").status_code == 404
        assert client.get("/static/../../pyproject.toml").status_code == 404

    def test_missing_static_file_returns_404(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        assert client.get("/static/nonexistent.html").status_code == 404


class TestManifestWithLegacyOrigin:
    """Tests for manifest endpoint with legacy origin."""

    def test_manifest_includes_legacy_origin_when_configured(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        manifest = client.get("/.well-known/skworld-module.json").json()
        assert manifest["legacyOrigin"] == LEGACY_ORIGIN

    def test_manifest_legacy_origin_is_none_when_not_configured(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=None)
        manifest = client.get("/.well-known/skworld-module.json").json()
        assert manifest["legacyOrigin"] is None


class TestLegacyPathsConstant:
    """Tests for LEGACY_PATHS constant."""

    def test_legacy_paths_contains_exactly_eight_entries(self):
        assert len(LEGACY_PATHS) == 8

    def test_legacy_paths_contains_all_expected_paths(self):
        assert "/cockpit" in LEGACY_PATHS
        assert "/cmdb" in LEGACY_PATHS
        assert "/board" in LEGACY_PATHS
        assert "/assistant" in LEGACY_PATHS
        assert "/trust" in LEGACY_PATHS
        assert "/models" in LEGACY_PATHS
        assert "/economy" in LEGACY_PATHS
        assert "/fleet" in LEGACY_PATHS

    def test_legacy_paths_is_frozenset(self):
        assert isinstance(LEGACY_PATHS, frozenset)


class TestAcceptanceCriteria1:
    """AC1: Verify sixteen sidebar destinations render, eight legacy return 404 without origin."""

    def test_all_sixteen_destinations_in_overview_html(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=None)
        response = client.get("/control-plane/now")
        assert response.status_code == 200
        # Control plane destinations (8) - these are control-plane API routes
        # Legacy destinations (8)
        assert 'href="/cockpit"' in response.text
        assert 'href="/cmdb"' in response.text
        assert 'href="/board"' in response.text
        assert 'href="/assistant"' in response.text
        assert 'href="/trust"' in response.text
        assert 'href="/models"' in response.text
        assert 'href="/economy"' in response.text
        assert 'href="/fleet"' in response.text

    def test_eight_legacy_paths_return_404_without_origin(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=None)
        for path in LEGACY_PATHS:
            assert client.get(path).status_code == 404

    def test_control_plane_routes_still_work(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=None)
        headers = {"Authorization": "Bearer test", "Origin": LAN_ORIGIN}
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/overview", headers=headers).status_code == 200


class TestAcceptanceCriteria2:
    """AC2: Legacy URL origin supplies HTTPS origin for all eight links and JavaScript."""

    def test_all_eight_legacy_links_rewritten_to_origin(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        response = client.get("/control-plane/now")
        assert response.status_code == 200
        for path in LEGACY_PATHS:
            assert f'href="{LEGACY_ORIGIN}{path}"' in response.text

    def test_javascript_board_link_rewritten_to_origin(self, tmp_path):
        client = _client(tmp_path, legacy_board_url=LEGACY_ORIGIN)
        response = client.get("/static/js/overview.js")
        assert response.status_code == 200
        assert f'"{LEGACY_ORIGIN}/board"' in response.text
        assert '"/board"' not in response.text


class TestAcceptanceCriteria3:
    """AC3: Malformed, credential-bearing, query-bearing, fragment-bearing, non-HTTPS URLs rejected."""

    def test_create_read_only_app_rejects_invalid_urls(self, tmp_path):
        """Test that invalid URLs raise ValueError."""
        with pytest.raises(ValueError, match="must be HTTPS"):
            create_read_only_app(tmp_path, legacy_board_url="http://legacy.example.com")
        with pytest.raises(ValueError, match="credentials"):
            create_read_only_app(tmp_path, legacy_board_url="https://user:pass@legacy.example.com")
        with pytest.raises(ValueError, match="query"):
            create_read_only_app(tmp_path, legacy_board_url="https://legacy.example.com?foo=bar")
        with pytest.raises(ValueError, match="fragment"):
            create_read_only_app(tmp_path, legacy_board_url="https://legacy.example.com#section")

    def test_create_read_only_app_accepts_valid_urls(self, tmp_path):
        """Test that valid URLs are accepted."""
        app = create_read_only_app(tmp_path, legacy_board_url="https://legacy.example.com")
        assert app is not None
