from __future__ import annotations

import asyncio
import threading
import time

import pytest

from skdashboard.workload_isolation import (
    BoundedWorkloadManager,
    WorkloadLimits,
    WorkloadQueueFull,
    WorkloadTimedOut,
)


def test_workload_admission_bounds_queue_and_completes() -> None:
    async def run() -> None:
        manager = BoundedWorkloadManager(
            {"insight": WorkloadLimits(max_concurrency=1, max_queue=2, timeout_seconds=1)}
        )
        started = threading.Event()
        release = threading.Event()

        def operation() -> str:
            started.set()
            release.wait(1)
            return "ok"

        try:
            first = asyncio.create_task(manager.run("insight", operation))
            assert await asyncio.to_thread(started.wait, 1)
            second = asyncio.create_task(manager.run("insight", operation))
            for _ in range(100):
                if manager.snapshot()["insight"]["pending"] == 2:
                    break
                await asyncio.sleep(0.001)
            assert manager.snapshot()["insight"]["pending"] == 2
            with pytest.raises(WorkloadQueueFull):
                await manager.run("insight", lambda: "rejected")
            release.set()
            assert await first == "ok"
            assert await second == "ok"
            snapshot = manager.snapshot()["insight"]
            assert snapshot["pending"] == 0
            assert snapshot["rejected"] == 1
            assert snapshot["completed"] == 2
        finally:
            release.set()
            manager.close()

    asyncio.run(run())


def test_workload_timeout_retains_bound_until_worker_finishes() -> None:
    async def run() -> None:
        manager = BoundedWorkloadManager(
            {"report": WorkloadLimits(max_concurrency=1, max_queue=1, timeout_seconds=0.01)}
        )
        try:
            with pytest.raises(WorkloadTimedOut):
                await manager.run("report", lambda: time.sleep(0.05))
            assert manager.snapshot()["report"]["pending"] == 1
            await asyncio.sleep(0.06)
            snapshot = manager.snapshot()["report"]
            assert snapshot["pending"] == 0
            assert snapshot["timeouts"] == 1
        finally:
            manager.close()

    asyncio.run(run())


def test_workload_cancellation_is_observable_and_does_not_release_early() -> None:
    async def run() -> None:
        manager = BoundedWorkloadManager(
            {"insight": WorkloadLimits(max_concurrency=1, max_queue=1, timeout_seconds=1)}
        )
        started = threading.Event()
        release = threading.Event()

        def operation() -> None:
            started.set()
            release.wait(1)

        try:
            task = asyncio.create_task(manager.run("insight", operation))
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert manager.snapshot()["insight"]["pending"] == 1
            release.set()
            await asyncio.sleep(0.06)
            snapshot = manager.snapshot()["insight"]
            assert snapshot["pending"] == 0
            assert snapshot["cancelled"] == 1
        finally:
            release.set()
            manager.close()

    asyncio.run(run())


def test_workload_failure_is_counted_and_released() -> None:
    async def run() -> None:
        manager = BoundedWorkloadManager(
            {"report": WorkloadLimits(max_concurrency=1, max_queue=1, timeout_seconds=1)}
        )
        try:
            with pytest.raises(RuntimeError, match="synthetic failure"):
                await manager.run("report", lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
            snapshot = manager.snapshot()["report"]
            assert snapshot["pending"] == 0
            assert snapshot["failed"] == 1
        finally:
            manager.close()

    asyncio.run(run())
