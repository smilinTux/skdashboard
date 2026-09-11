import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from skdashboard import dashboard_assistant
from skdashboard.control_plane_api import routes


class FakeAssistant:
    def chat(self, messages, **kwargs):
        assert kwargs["max_tokens"] == 1200
        assert kwargs["response_schema"]["title"] == "NowOperatorBrief"
        facts = json.loads(messages[1]["content"].split("\n", 1)[1])["facts"]
        assert len(facts) <= dashboard_assistant.MAX_NOW_FACTS
        source = {key: facts[0][key] for key in ("source_id", "observed_at", "freshness")}
        return json.dumps(
            {
                "schema_version": "skdashboard.now-operator-brief.v1",
                "status": "proposal",
                "generated_at": "2026-09-07T17:00:00Z",
                "conditions": [
                    {
                        "summary": "Work is active",
                        "sources": [source],
                        "uncertainty": "Point-in-time aggregate",
                    }
                ],
                "risks": [],
                "anomalies": [],
                "next_steps": [
                    {
                        "rank": 1,
                        "proposal": "Review blocked work",
                        "summary": "Confirm owners",
                        "sources": [source],
                        "uncertainty": "No task mutation is authorized",
                    }
                ],
                "abstention": None,
            }
        )


class StaticAssistant:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, _messages, **_kwargs):
        return json.dumps(self.payload)


def _reference(source_id="source.current", freshness="current"):
    return {
        "source_id": source_id,
        "observed_at": "2026-09-10T20:00:00Z",
        "freshness": freshness,
    }


def _overview(*states):
    return {
        "scope": {"role": "operator", "scope": "estate"},
        "items": [
            {
                "adapter_id": f"source.{state}",
                "truth_state": state,
                "observed_at": "2026-09-10T20:00:00Z",
                "aggregate": {"count": 1},
                "coverage": {"reporting": 1, "expected": 1},
                "errors": [],
            }
            for state in states
        ],
    }


def _proposal(source_id="source.current", freshness="current", **updates):
    payload = {
        "schema_version": "skdashboard.now-operator-brief.v1",
        "status": "proposal",
        "generated_at": "2026-09-10T20:00:00Z",
        "conditions": [
            {
                "summary": "Current evidence needs review",
                "sources": [_reference(source_id, freshness)],
                "uncertainty": "Aggregate evidence only",
            }
        ],
        "risks": [],
        "anomalies": [],
        "next_steps": [],
        "abstention": None,
    }
    payload.update(updates)
    return payload


def test_mixed_current_and_partial_evidence_yields_bounded_proposal(monkeypatch):
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal("source.partial", "partial")),
    )

    result = dashboard_assistant.now_operator_brief(
        _overview("current", "partial", "unavailable")
    )

    assert result["status"] == "proposal"
    assert result["conditions"][0]["sources"][0]["source_id"] == "source.partial"


def test_usable_evidence_cannot_be_replaced_by_abstention(monkeypatch):
    payload = {
        "schema_version": "skdashboard.now-operator-brief.v1",
        "status": "abstained",
        "generated_at": "2026-09-10T20:00:00Z",
        "conditions": [],
        "risks": [],
        "anomalies": [],
        "next_steps": [],
        "abstention": "insufficient evidence",
    }
    monkeypatch.setattr(
        dashboard_assistant, "get_client", lambda: StaticAssistant(payload)
    )

    with pytest.raises(ValueError, match="abstained despite usable evidence"):
        dashboard_assistant.now_operator_brief(_overview("partial"))


def test_total_insufficiency_allows_empty_abstention(monkeypatch):
    payload = {
        "schema_version": "skdashboard.now-operator-brief.v1",
        "status": "abstained",
        "generated_at": "2026-09-10T20:00:00Z",
        "conditions": [],
        "risks": [],
        "anomalies": [],
        "next_steps": [],
        "abstention": "no usable evidence",
    }
    monkeypatch.setattr(
        dashboard_assistant, "get_client", lambda: StaticAssistant(payload)
    )

    result = dashboard_assistant.now_operator_brief(
        _overview("stale", "unavailable")
    )

    assert result["status"] == "abstained"


def test_unusable_or_unauthorized_citation_is_rejected(monkeypatch):
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal("source.unavailable", "unavailable")),
    )

    with pytest.raises(ValueError, match="unauthorized source"):
        dashboard_assistant.now_operator_brief(_overview("current", "unavailable"))


def test_changed_source_provenance_is_rejected(monkeypatch):
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal(freshness="partial")),
    )

    with pytest.raises(ValueError, match="changed cited source provenance"):
        dashboard_assistant.now_operator_brief(_overview("current"))


def test_write_action_is_rejected(monkeypatch):
    step = {
        "rank": 1,
        "proposal": "Restart the gateway",
        "read_only": True,
        "summary": "Restore service",
        "sources": [_reference()],
        "uncertainty": "No action is authorized",
    }
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal(next_steps=[step])),
    )

    with pytest.raises(ValueError, match="not read-only"):
        dashboard_assistant.now_operator_brief(_overview("current"))


@pytest.mark.parametrize("proposal", ["Run systemctl status", "Issue a command now"])
def test_command_is_rejected(monkeypatch, proposal):
    step = {
        "rank": 1,
        "proposal": proposal,
        "read_only": True,
        "summary": "Inspect service",
        "sources": [_reference()],
        "uncertainty": "No action is authorized",
    }
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal(next_steps=[step])),
    )

    with pytest.raises(ValueError, match="not read-only"):
        dashboard_assistant.now_operator_brief(_overview("current"))


def test_malformed_typed_response_is_rejected(monkeypatch):
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant({"status": "proposal", "conditions": "wrong"}),
    )

    with pytest.raises(ValueError):
        dashboard_assistant.now_operator_brief(_overview("current"))


def test_unsupported_causal_claim_is_rejected(monkeypatch):
    condition = {
        "summary": "Queue growth caused the outage",
        "sources": [_reference()],
        "uncertainty": "No causal experiment exists",
    }
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal(conditions=[condition])),
    )

    with pytest.raises(ValueError, match="unsupported causal claim"):
        dashboard_assistant.now_operator_brief(_overview("current"))


def test_unsupported_causal_next_step_is_rejected(monkeypatch):
    step = {
        "rank": 1,
        "proposal": "Review latency because of queue growth",
        "read_only": True,
        "summary": "Compare current evidence",
        "sources": [_reference()],
        "uncertainty": "No causal experiment exists",
    }
    monkeypatch.setattr(
        dashboard_assistant,
        "get_client",
        lambda: StaticAssistant(_proposal(next_steps=[step])),
    )

    with pytest.raises(ValueError, match="unsupported causal claim"):
        dashboard_assistant.now_operator_brief(_overview("current"))


def test_now_ai_brief_is_protected_typed_and_read_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_assistant, "get_client", lambda: FakeAssistant())
    app = Starlette(
        routes=routes(
            tmp_path,
            board_reader=lambda _home: {"tasks": []},
            health_reader=lambda _home: {"consciousness": "active", "pillars": {}},
            authorizer=lambda *_args: True,
        )
    )
    client = TestClient(app)
    assert client.get("/api/v1/now/ai-brief").status_code == 401
    response = client.get(
        "/api/v1/now/ai-brief?role=operator&scope=estate&window=latest&baseline=none&service=all",
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200
    assert response.json()["next_steps"][0]["rank"] == 1
    assert client.post(
        "/api/v1/now/ai-brief", headers={"Authorization": "Bearer test"}
    ).status_code == 405


def test_now_ai_brief_does_not_block_other_requests(tmp_path: Path, monkeypatch) -> None:
    started = False

    def slow_brief(_aggregate, actor):
        nonlocal started
        started = True
        time.sleep(0.3)
        return {"status": "abstained", "generated_at": "2026-09-07T17:00:00Z"}

    monkeypatch.setattr(dashboard_assistant, "now_operator_brief", slow_brief)
    app = Starlette(
        routes=routes(
            tmp_path,
            board_reader=lambda _home: {"tasks": []},
            health_reader=lambda _home: {"consciousness": "active", "pillars": {}},
            authorizer=lambda *_args: True,
        )
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(
            client.get,
            "/api/v1/now/ai-brief",
            headers={"Authorization": "Bearer test"},
        )
        deadline = time.monotonic() + 1
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        before = time.monotonic()
        health = client.get("/api/v1/health")
        elapsed = time.monotonic() - before

        assert health.status_code == 200
        assert elapsed < 0.2
        assert request.result().status_code == 200
