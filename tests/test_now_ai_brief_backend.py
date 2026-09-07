import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from skdashboard import dashboard_assistant
from skdashboard.control_plane_api import routes


class FakeAssistant:
    def chat(self, messages, **kwargs):
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
