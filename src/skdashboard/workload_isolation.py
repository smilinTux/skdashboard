"""Bounded local executors for expensive read-plane workloads."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable


class WorkloadError(RuntimeError):
    """Base class for bounded workload admission failures."""


class WorkloadQueueFull(WorkloadError):
    """The bounded admission queue has no remaining capacity."""


class WorkloadTimedOut(WorkloadError):
    """The workload exceeded its execution timeout."""


@dataclass(frozen=True)
class WorkloadLimits:
    """Admission and execution bounds for one workload class."""

    max_concurrency: int
    max_queue: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if self.max_queue < self.max_concurrency:
            raise ValueError("max_queue must cover max_concurrency")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


DEFAULT_WORKLOAD_LIMITS = {
    "insight": WorkloadLimits(max_concurrency=2, max_queue=8, timeout_seconds=5.0),
    "report": WorkloadLimits(max_concurrency=1, max_queue=4, timeout_seconds=10.0),
}


class BoundedWorkloadManager:
    """Run synchronous owner work behind bounded, observable executors.

    Admission counts running and queued work together. A timeout or caller
    cancellation releases admission only after the executor task actually
    finishes, so an abandoned thread cannot silently exceed the ceiling.
    """

    def __init__(
        self,
        limits: dict[str, WorkloadLimits] | None = None,
    ) -> None:
        self.limits = dict(limits or DEFAULT_WORKLOAD_LIMITS)
        if not self.limits:
            raise ValueError("at least one workload limit is required")
        self._executors = {
            name: ThreadPoolExecutor(
                max_workers=config.max_concurrency,
                thread_name_prefix=f"skdashboard-{name}",
            )
            for name, config in self.limits.items()
        }
        self._lock = threading.Lock()
        self._pending = {name: 0 for name in self.limits}
        self._stats = {
            name: {
                "accepted": 0,
                "completed": 0,
                "rejected": 0,
                "timeouts": 0,
                "cancelled": 0,
                "failed": 0,
            }
            for name in self.limits
        }

    async def run(self, workload: str, operation: Callable[[], object]) -> object:
        """Run one operation or raise a bounded workload error."""
        if workload not in self.limits:
            raise ValueError(f"unknown workload: {workload}")
        config = self.limits[workload]
        with self._lock:
            if self._pending[workload] >= config.max_queue:
                self._stats[workload]["rejected"] += 1
                raise WorkloadQueueFull(workload)
            self._pending[workload] += 1
            self._stats[workload]["accepted"] += 1
        try:
            future = self._executors[workload].submit(operation)
        except Exception:
            self._release(workload, failed=True)
            raise
        future.add_done_callback(lambda _future: self._release(workload))
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(future), timeout=config.timeout_seconds
            )
        except asyncio.TimeoutError as error:
            with self._lock:
                self._stats[workload]["timeouts"] += 1
            raise WorkloadTimedOut(workload) from error
        except asyncio.CancelledError:
            with self._lock:
                self._stats[workload]["cancelled"] += 1
            raise
        except Exception:
            with self._lock:
                self._stats[workload]["failed"] += 1
            raise

    def _release(self, workload: str, *, failed: bool = False) -> None:
        with self._lock:
            self._pending[workload] -= 1
            if failed:
                self._stats[workload]["failed"] += 1
            else:
                self._stats[workload]["completed"] += 1

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        """Return bounded admission and outcome counters."""
        with self._lock:
            return {
                name: {
                    "max_concurrency": config.max_concurrency,
                    "max_queue": config.max_queue,
                    "timeout_seconds": config.timeout_seconds,
                    "pending": self._pending[name],
                    **self._stats[name],
                }
                for name, config in self.limits.items()
            }

    def close(self) -> None:
        """Stop accepting work and cancel executor work not yet started."""
        for executor in self._executors.values():
            executor.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "BoundedWorkloadManager",
    "DEFAULT_WORKLOAD_LIMITS",
    "WorkloadError",
    "WorkloadLimits",
    "WorkloadQueueFull",
    "WorkloadTimedOut",
]
