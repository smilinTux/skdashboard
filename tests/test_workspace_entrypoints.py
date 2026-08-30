from pathlib import Path

from starlette.testclient import TestClient

from skdashboard.dashboard import create_app


def test_legacy_workspace_entrypoints_route_to_real_read_sources(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    matters = client.get("/matters", follow_redirects=False)
    tasks = client.get("/tasks", follow_redirects=False)
    queue = client.get("/work-queue", follow_redirects=False)

    assert matters.status_code == tasks.status_code == queue.status_code == 307
    assert matters.headers["location"].endswith("selected_silo=legal")
    assert tasks.headers["location"] == "/board"
    assert queue.headers["location"] == "/board"
    assert all(response.headers["cache-control"] == "no-store" for response in (matters, tasks, queue))
