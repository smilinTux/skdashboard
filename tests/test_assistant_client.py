"""Read-only, provider-neutral assistant contract tests for card b677a99f."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skdashboard import dashboard_assistant
from skdashboard.assistant_client import (
    DEFAULT_ROUTE,
    AssistantClient,
    AssistantClientError,
    configured_route,
    validate_messages,
)


def test_logical_route_is_deployment_configured_and_bounded(monkeypatch) -> None:
    monkeypatch.delenv("SKDASHBOARD_ASSISTANT_ROUTE", raising=False)
    assert configured_route() == DEFAULT_ROUTE
    monkeypatch.setenv("SKDASHBOARD_ASSISTANT_ROUTE", "dashboard-observer")
    assert configured_route() == "dashboard-observer"
    for invalid in ("", "https://provider.test/v1", "Model Name", "x" * 129):
        monkeypatch.setenv("SKDASHBOARD_ASSISTANT_ROUTE", invalid)
        with pytest.raises(ValueError):
            configured_route()


def test_exact_request_contract_excludes_tools_credentials_and_protected_payloads() -> None:
    assert validate_messages([{"role": "user", "content": "Summarize aggregate flow"}]) == [
        {"role": "user", "content": "Summarize aggregate flow"}
    ]
    invalid = [
        {"role": "user", "content": "hello", "tools": []},
        {"role": "user", "content": "Authorization: Bearer example"},
        {"role": "user", "content": "x-sk-capability: example"},
        {"role": "user", "content": '{"prompt":"protected"}'},
        {"role": "tool", "content": "run"},
    ]
    for message in invalid:
        with pytest.raises(ValueError):
            validate_messages([message])


def test_client_uses_only_shared_skgateway_abstraction(monkeypatch) -> None:
    calls = []

    def stream(messages, **kwargs):
        calls.append((messages, kwargs))
        yield "observed"

    monkeypatch.setattr("skcapstone.skgateway_client.chat_stream", stream)
    assert list(
        AssistantClient().chat_stream(
            [{"role": "user", "content": "Summarize aggregate flow"}], actor="casey"
        )
    ) == ["observed"]
    assert calls == [
        (
            [{"role": "user", "content": "Summarize aggregate flow"}],
            {
                "model": DEFAULT_ROUTE,
                "max_tokens": 1400,
                "temperature": 0.3,
                "timeout": 90.0,
            },
        )
    ]


def test_route_override_cannot_bypass_configured_gateway_route(monkeypatch) -> None:
    monkeypatch.setenv("SKDASHBOARD_ASSISTANT_ROUTE", DEFAULT_ROUTE)
    client = AssistantClient(route="direct-provider-model")
    with pytest.raises(AssistantClientError) as raised:
        list(client.chat_stream([{"role": "user", "content": "hello"}], actor="casey"))
    assert raised.value.reason == "invalid_request"
    assert "direct-provider-model" not in json.dumps(raised.value.audit_context)


def test_assistant_module_has_no_action_or_mutation_surface() -> None:
    source = Path(dashboard_assistant.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "_parse_action",
        "_run_action",
        "request_run(",
        "apply_mutation(",
        "capability_ok",
        'event: action',
    ):
        assert forbidden not in source


def test_stream_answer_contains_only_token_and_done_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard_assistant, "build_context", lambda _home: "COUNT: 3")

    class Client:
        def chat_stream(self, messages, *, actor):
            assert actor == "casey"
            assert messages[1]["content"].startswith("AGGREGATE SNAPSHOT:")
            yield "Three aggregate items."

    monkeypatch.setattr("skdashboard.assistant_client.get_client", lambda: Client())
    frames = list(dashboard_assistant.stream_answer(tmp_path, "How many?", actor="casey"))
    assert [frame.splitlines()[0] for frame in frames] == ["event: token", "event: done"]
    assert all("action" not in frame.lower() for frame in frames)


def test_context_contains_aggregate_counts_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        dashboard_assistant,
        "board_summary",
        lambda _home: {
            "active": 3,
            "by_column": {"doing": 2, "review": 1},
            "by_lane": {"standard": 3},
            "wip": {"doing": {"count": 2, "limit": 8, "over": False}},
        },
    )
    monkeypatch.setattr(
        "skdashboard.dashboard_itil.get_overview",
        lambda _home: {
            "kpis": {"open_incidents": 1},
            "by_severity": {"sev1": 0, "sev2": 1},
            "breach_risk": [{"id": "protected-incident"}],
            "cab_queue": [{"id": "protected-change"}],
        },
    )
    context = dashboard_assistant.build_context(tmp_path)
    assert json.loads(context) == {
        "itil": {
            "by_severity": {"sev1": 0, "sev2": 1},
            "kpis": {"open_incidents": 1},
        },
        "kanban": {
            "active": 3,
            "by_column": {"doing": 2, "review": 1},
            "by_lane": {"standard": 3},
            "wip": {"doing": {"count": 2, "limit": 8, "over": False}},
        },
    }
    assert "protected-incident" not in context
    assert "protected-change" not in context


def test_assistant_endpoint_is_capability_gated_and_read_only(monkeypatch, tmp_path) -> None:
    from starlette.testclient import TestClient

    from skdashboard.dashboard import create_app

    called = []
    monkeypatch.setattr(
        dashboard_assistant,
        "stream_answer",
        lambda *_args, **_kwargs: called.append(True) or iter(["event: done\ndata: {}\n\n"]),
    )
    monkeypatch.setenv("SKAI_AUTHZ", "token")
    monkeypatch.setenv("SKAI_QUEUE_TOKEN", "test-read-capability")
    denied = TestClient(create_app(tmp_path))
    assert denied.post("/api/assistant", json={"prompt": "hello"}).status_code == 403
    assert called == []

    response = denied.post(
        "/api/assistant",
        json={"prompt": "hello"},
        headers={"x-sk-capability": "test-read-capability"},
    )
    assert response.status_code == 200
    assert called == [True]
