from pathlib import Path

from skdashboard import dashboard_assistant as assistant


def test_assistant_defaults_to_skgateway_public_route(monkeypatch, tmp_path: Path):
    observed = {}

    def fake_stream(messages, **kwargs):
        observed.update(kwargs)
        yield "report"

    monkeypatch.setattr(assistant, "build_context", lambda _home: "LIVE")
    monkeypatch.setattr("skcapstone.skgateway_client.chat_stream", fake_stream)

    list(assistant.stream_answer(tmp_path, "status", capability_ok=False))

    assert observed["model"] == "sk-m-public"
    assert observed["base_url"] == "http://chiap01:18790/v1"
