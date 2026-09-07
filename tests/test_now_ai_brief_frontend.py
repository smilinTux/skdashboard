from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_now_page_offers_one_click_accessible_ai_operator_brief() -> None:
    html = (ROOT / "src/skdashboard/static/overview.html").read_text(encoding="utf-8")
    javascript = (ROOT / "src/skdashboard/static/js/overview.js").read_text(encoding="utf-8")
    css = (ROOT / "src/skdashboard/static/css/overview.css").read_text(encoding="utf-8")

    assert 'id="ai-analyze-button"' in html
    assert 'id="ai-analysis"' in html
    assert 'aria-live="polite"' in html
    assert "/api/v1/now/ai-brief" in javascript
    assert "Recommended next steps" in javascript
    assert "Proposals only; no action was taken." in javascript
    assert ".ai-analysis" in css
