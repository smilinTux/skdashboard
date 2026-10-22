from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from skdashboard.dashboard import create_app
from skdashboard.dashboard_kanban import (
    SSE_BOUND,
    Bus,
    StreamReset,
    stream_sse,
)

EVENT_HEADERS = {
    "Authorization": "Bearer valid-events",
    "Origin": "https://100.81.238.58:7778",
}


def _authorizer(bearer: str, capability: str, _target: str) -> bool:
    return bearer == "valid-events" and capability == "skdashboard.events.read"


def test_ids_replay_resume_and_explicit_reset_are_exact_and_bounded() -> None:
    bus = Bus(stream_id="a" * 32)
    events = [bus.publish({"type": "board", "value": value}) for value in range(3)]
    assert all(events)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert len({event.event_id for event in events}) == 3
    assert all(not event.event_id.endswith(str(event.sequence)) for event in events)

    resumed = bus.open_stream(events[0].event_id)
    assert resumed.replay == tuple(events[1:])
    resumed.close()

    for value in range(3, SSE_BOUND + 2):
        bus.publish({"type": "board", "value": value})
    assert bus.replay_size == SSE_BOUND
    expired = bus.open_stream(events[0].event_id)
    assert expired.queue is None
    assert expired.reset == StreamReset("replay window unavailable")

    other = Bus(stream_id="b" * 32).publish({"type": "board"})
    unknown_after_rollback = bus.open_stream(other.event_id)
    assert unknown_after_rollback.reset == StreamReset("replay window unavailable")


def test_reconnect_topics_concurrent_consumers_and_slow_consumer_are_bounded() -> None:
    bus = Bus(stream_id="c" * 32)
    first = bus.publish({"type": "board", "value": 1})
    bus.publish({"type": "reports", "value": 2})
    filtered = bus.open_stream(first.event_id, ("reports",))
    assert [event.event for event in filtered.replay] == ["reports"]
    filtered.close()

    one = bus.open_stream()
    two = bus.open_stream()
    live = bus.publish({"type": "board", "value": 3})
    assert one.queue.get_nowait() == live
    assert two.queue.get_nowait() == live

    for value in range(SSE_BOUND + 1):
        bus.publish({"type": "board", "value": value})
    reset = one.queue.get_nowait()
    assert reset == StreamReset("slow consumer")
    assert one.queue.qsize() == 0
    assert two.queue.qsize() <= SSE_BOUND
    assert bus.replay_size == SSE_BOUND
    assert bus.subscriber_count == 0

    subscriptions = [bus.open_stream() for _ in range(SSE_BOUND)]
    with pytest.raises(RuntimeError, match="capacity"):
        bus.open_stream()
    for subscription in subscriptions:
        subscription.close()


def test_heartbeat_cancellation_and_producer_failure_cleanup() -> None:
    async def run() -> None:
        bus = Bus(stream_id="d" * 32)
        subscription = bus.open_stream()
        body = stream_sse(subscription, heartbeat_seconds=0)
        assert await anext(body) == ": heartbeat\n\n"
        assert await anext(body) == ": heartbeat\n\n"
        await body.aclose()
        assert bus.subscriber_count == 0

        subscription = bus.open_stream()
        body = stream_sse(subscription)
        assert bus.publish({"type": "board", "bad": object()}) is None
        assert await anext(body) == ": heartbeat\n\n"
        assert "producer failure" in await anext(body)
        with pytest.raises(StopAsyncIteration):
            await anext(body)
        assert bus.subscriber_count == 0

    asyncio.run(run())


def test_last_event_id_reset_and_invalid_cursors_follow_http_contract(
    tmp_path, monkeypatch
) -> None:
    from skdashboard import dashboard_kanban

    bus = Bus(stream_id="e" * 32)
    monkeypatch.setattr(dashboard_kanban, "BUS", bus)
    foreign = Bus(stream_id="f" * 32).publish({"type": "board"})
    client = TestClient(create_app(tmp_path, control_plane_authorizer=_authorizer))

    reset = client.get(
        "/api/v1/events", headers={**EVENT_HEADERS, "Last-Event-ID": foreign.event_id}
    )
    assert reset.status_code == 200
    assert reset.headers["x-heartbeat-interval"] == "20"
    assert reset.text == (
        'event: reset-required\ndata: {"reason":"replay window unavailable"}\n\n: heartbeat\n\n'
    )
    assert (
        client.get("/api/v1/events", headers={**EVENT_HEADERS, "Last-Event-ID": "%%%"}).status_code
        == 400
    )
    assert (
        client.get(
            "/api/v1/events", headers={**EVENT_HEADERS, "Last-Event-ID": "x" * 513}
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/api/v1/events?cursor=djE6MQ",
            headers={**EVENT_HEADERS, "Last-Event-ID": foreign.event_id},
        ).status_code
        == 400
    )
