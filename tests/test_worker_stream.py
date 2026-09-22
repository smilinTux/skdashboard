"""Server-side fan-in for the projected fleet worker stream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from skdashboard import worker_stream
from skdashboard.dashboard import create_app


class _Stdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    async def readline(self) -> bytes:
        return next(self._lines, b"")


class _Process:
    def __init__(self, *, output: bytes = b"", lines: list[bytes] | None = None) -> None:
        self._output = output
        self.stdout = _Stdout(lines or [])
        self.returncode = 0
        self.terminated = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._output, b""

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    async def wait(self) -> int:
        return self.returncode


def _snapshot(host: str, card: str, *, session: bool) -> bytes:
    row = {
        "host": host,
        "card": card,
        "unit": f"skfleet-worker-codex-m-{card}.service",
        "state": "active",
        "events": 0,
        "silent_s": None,
    }
    if session:
        row["session"] = "current.jsonl"
    else:
        row["note"] = "no session file for this run"
    return (json.dumps(row) + "\n").encode()


def test_stream_fans_in_five_tail_started_projectors() -> None:
    card = "ae43ecbf"
    commands: list[tuple[str, ...]] = []
    follower_processes: list[_Process] = []

    async def spawn(*command: str, **_kwargs):
        commands.append(command)
        host = next(value for value in worker_stream.HOSTS if value in command)
        if "--follow" not in command:
            process = _Process(output=_snapshot(host, card, session=True))
        else:
            row = {
                "ts": f"2026-09-22T15:00:0{len(follower_processes)}Z",
                "host": host,
                "card": card,
                "role": "assistant",
                "tool": "bash",
                "preview": f"live from {host}",
            }
            process = _Process(lines=[(json.dumps(row) + "\n").encode()])
            follower_processes.append(process)
        return process

    events = asyncio.run(worker_stream.collect(card, spawn=spawn))

    follow_commands = [command for command in commands if "--follow" in command]
    assert {
        next(host for host in worker_stream.HOSTS if host in command)
        for command in follow_commands
    } == set(worker_stream.HOSTS)
    assert all("--from-start" not in command for command in follow_commands)
    assert {event["host"] for event in events if event["type"] == "activity"} == set(
        worker_stream.HOSTS
    )
    assert all(event["card"] == card for event in events)


def test_current_worker_without_session_is_explicitly_no_stream() -> None:
    card = "deadbee1"

    async def spawn(*command: str, **_kwargs):
        host = next(value for value in worker_stream.HOSTS if value in command)
        if "--follow" in command:
            return _Process()
        return _Process(output=_snapshot(host, card, session=host != "chiap03"))

    events = asyncio.run(worker_stream.collect(card, spawn=spawn))
    states = {event["host"]: event for event in events if event["type"] == "state"}

    assert states["chiap03"] == {
        "type": "state",
        "host": "chiap03",
        "card": card,
        "state": "no-stream",
        "reason": "no-session-file-for-current-run",
    }


def test_stream_rejects_spoofed_hosts_other_cards_and_unprojected_rows() -> None:
    card = "ae43ecbf"

    async def spawn(*command: str, **_kwargs):
        host = next(value for value in worker_stream.HOSTS if value in command)
        if "--follow" not in command:
            return _Process(output=_snapshot(host, card, session=True))
        rows = [
            {"ts": "1", "host": host, "card": "ffffffff", "preview": "other card"},
            {"ts": "2", "host": "attacker", "card": card, "preview": "spoofed host"},
            {"ts": "3", "host": host, "card": card, "raw": {"secret": "payload"}},
            {
                "ts": "4",
                "host": host,
                "card": card,
                "role": "toolResult",
                "tool": "read",
                "preview": "bounded projection",
            },
        ]
        return _Process(lines=[(json.dumps(row) + "\n").encode() for row in rows])

    events = asyncio.run(worker_stream.collect(card, spawn=spawn))
    activity = [event for event in events if event["type"] == "activity"]

    assert len(activity) == len(worker_stream.HOSTS)
    assert {event["preview"] for event in activity} == {"bounded projection"}
    assert all(set(event) <= worker_stream.PROJECTED_FIELDS | {"type"} for event in activity)


def test_card_id_is_bounded_before_any_process_starts() -> None:
    called = False

    async def spawn(*_command: str, **_kwargs):
        nonlocal called
        called = True
        return _Process()

    try:
        asyncio.run(worker_stream.collect("../../raw-session", spawn=spawn))
    except ValueError as exc:
        assert str(exc) == "card must be eight lowercase hexadecimal characters"
    else:
        raise AssertionError("invalid card id accepted")
    assert called is False


def test_protected_api_serves_sse_without_buffering_or_caching(tmp_path: Path) -> None:
    async def finite_stream(card: str):
        yield {
            "type": "state",
            "host": "chiap01",
            "card": card,
            "state": "no-stream",
            "reason": "no-session-file-for-current-run",
        }
        yield None

    app = create_app(tmp_path, control_plane_authorizer=lambda *_args: True)
    headers = {"Authorization": "Bearer test", "Origin": "https://10.0.0.139:7778"}
    with patch("skdashboard.worker_stream.stream", side_effect=finite_stream):
        response = TestClient(app).get(
            "/api/v1/fleet/worker-stream/ae43ecbf",
            headers=headers,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: state" in response.text
    assert '"state":"no-stream"' in response.text
    assert response.text.endswith(": heartbeat\n\n")


def test_worker_stream_api_rejects_invalid_card_before_streaming(tmp_path: Path) -> None:
    app = create_app(tmp_path, control_plane_authorizer=lambda *_args: True)
    response = TestClient(app).get(
        "/api/v1/fleet/worker-stream/not-a-card",
        headers={
            "Authorization": "Bearer test",
            "Origin": "https://10.0.0.139:7778",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_QUERY"
