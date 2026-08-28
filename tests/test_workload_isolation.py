from __future__ import annotations

import asyncio
import time
from threading import Event

import pytest

from skdashboard.workload_isolation import (
    BoundedWorkload,
    WorkloadLimits,
    WorkloadRequestTooLarge,
    WorkloadResponseTooLarge,
    WorkloadSaturated,
    WorkloadTimedOut,
)


def test_limits_reject_invalid_resource_contracts():
    with pytest.raises(ValueError):
        WorkloadLimits(0, 0, 1, 1, 1)
    with pytest.raises(ValueError):
        WorkloadLimits(1, -1, 1, 1, 1)
    with pytest.raises(ValueError):
        WorkloadLimits(1, 0, 0, 1, 1)


def test_saturation_is_immediate_bounded_and_observable():
    entered = Event()
    release = Event()
    workload = BoundedWorkload("synthetic", WorkloadLimits(1, 1, 1, 128, 128))

    def blocked(value):
        entered.set()
        release.wait(1)
        return {"value": value}

    async def run():
        first = asyncio.create_task(workload.run(blocked, 1, request_bytes=1))
        assert await asyncio.to_thread(entered.wait, 1)
        second = asyncio.create_task(workload.run(blocked, 2, request_bytes=1))
        await asyncio.sleep(0.02)
        started = time.monotonic()
        with pytest.raises(WorkloadSaturated):
            await workload.run(blocked, 3, request_bytes=1)
        assert time.monotonic() - started < 0.1
        release.set()
        assert await first == {"value": 1}
        assert await second == {"value": 2}

    asyncio.run(run())
    snapshot = workload.snapshot()
    assert snapshot["accepted"] == 2
    assert snapshot["completed"] == 2
    assert snapshot["saturated"] == 1
    assert snapshot["admitted"] == 0


def test_timeout_retains_slot_until_worker_returns():
    release = Event()
    workload = BoundedWorkload("timeout", WorkloadLimits(1, 0, 0.03, 128, 128))

    def blocked():
        release.wait(1)
        return {"late": True}

    async def run():
        with pytest.raises(WorkloadTimedOut):
            await workload.run(blocked, request_bytes=1)
        with pytest.raises(WorkloadSaturated):
            await workload.run(lambda: {"escaped": True}, request_bytes=1)
        assert workload.snapshot()["admitted"] == 1
        release.set()
        for _ in range(100):
            if workload.snapshot()["admitted"] == 0:
                break
            await asyncio.sleep(0.01)
        assert await workload.run(lambda: {"bounded": True}, request_bytes=1) == {
            "bounded": True
        }

    asyncio.run(run())
    assert workload.snapshot()["timed_out"] == 1


def test_timeout_returns_before_running_worker_and_bounds_subsequent_admission():
    release = Event()
    workload = BoundedWorkload("deadline", WorkloadLimits(1, 0, 0.03, 128, 128))

    async def run():
        started = time.monotonic()
        with pytest.raises(WorkloadTimedOut):
            await workload.run(lambda: (release.wait(1), {})[1], request_bytes=1)
        assert time.monotonic() - started < 0.2
        with pytest.raises(WorkloadSaturated):
            await workload.run(lambda: {}, request_bytes=1)
        release.set()

    asyncio.run(run())


def test_cancellation_size_and_failure_counters_are_explicit():
    release = Event()
    workload = BoundedWorkload("failures", WorkloadLimits(1, 1, 1, 4, 8))

    async def cancel_running():
        task = asyncio.create_task(
            workload.run(lambda: (release.wait(1), {"ok": True})[1], request_bytes=1)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    asyncio.run(cancel_running())
    with pytest.raises(WorkloadRequestTooLarge):
        asyncio.run(workload.run(lambda: {}, request_bytes=5))
    with pytest.raises(WorkloadResponseTooLarge):
        asyncio.run(workload.run(lambda: {"value": "too large"}, request_bytes=1))
    with pytest.raises(RuntimeError):
        asyncio.run(
            workload.run(
                lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
                request_bytes=1,
            )
        )
    snapshot = workload.snapshot()
    assert snapshot["cancelled"] == 1
    assert snapshot["request_too_large"] == 1
    assert snapshot["response_too_large"] == 1
    assert snapshot["failed"] == 1
