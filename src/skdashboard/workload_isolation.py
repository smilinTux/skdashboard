"""Bounded, observable isolation for expensive read-plane workloads."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Callable, TypeVar

T = TypeVar("T")


class WorkloadError(RuntimeError):
    """Base class for bounded workload failures."""


class WorkloadSaturated(WorkloadError):
    """No execution or queue slot is available."""


class WorkloadTimedOut(WorkloadError):
    """The bounded execution deadline elapsed."""


class WorkloadRequestTooLarge(WorkloadError):
    """The request exceeds the workload input ceiling."""


class WorkloadResponseTooLarge(WorkloadError):
    """The result exceeds the workload output ceiling."""


@dataclass(frozen=True)
class WorkloadLimits:
    """Immutable admission and resource limits for one workload class."""

    max_concurrency: int
    max_queue_depth: int
    timeout_seconds: float
    max_request_bytes: int
    max_response_bytes: int

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if self.max_queue_depth < 0:
            raise ValueError("max_queue_depth cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_request_bytes < 1 or self.max_response_bytes < 1:
            raise ValueError("request and response limits must be positive")


INSIGHT_LIMITS = WorkloadLimits(
    max_concurrency=2,
    max_queue_depth=4,
    timeout_seconds=15.0,
    max_request_bytes=64 * 1024,
    max_response_bytes=2 * 1024 * 1024,
)
REPORT_LIMITS = WorkloadLimits(
    max_concurrency=2,
    max_queue_depth=4,
    timeout_seconds=10.0,
    max_request_bytes=8 * 1024,
    max_response_bytes=2 * 1024 * 1024,
)


class BoundedWorkload:
    """Run blocking provider work in one dedicated bounded executor.

    Timed-out running calls retain their admission slot until the underlying
    thread returns. This prevents repeated timeouts from creating an unbounded
    hidden backlog. Cancellation is attempted for queued work and is always
    counted, but Python cannot forcibly terminate an already running thread.
    """

    _COUNTERS = (
        "accepted",
        "completed",
        "saturated",
        "timed_out",
        "cancelled",
        "failed",
        "request_too_large",
        "response_too_large",
    )

    def __init__(self, name: str, limits: WorkloadLimits) -> None:
        if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in name):
            raise ValueError("workload name must be a metric-safe identifier")
        self.name = name
        self.limits = limits
        self._executor = ThreadPoolExecutor(
            max_workers=limits.max_concurrency,
            thread_name_prefix=f"skdashboard-{name}",
        )
        self._slots = BoundedSemaphore(limits.max_concurrency + limits.max_queue_depth)
        self._lock = Lock()
        self._active = 0
        self._admitted = 0
        self._counters = {key: 0 for key in self._COUNTERS}

    def _increment(self, key: str) -> None:
        with self._lock:
            self._counters[key] += 1

    def _release(self, _future: Future) -> None:
        with self._lock:
            self._admitted -= 1
        self._slots.release()

    @staticmethod
    def _invoke(
        call: Callable[..., T],
        args: tuple,
        kwargs: dict,
        mark_active: Callable[[], None],
        mark_inactive: Callable[[], None],
        max_response_bytes: int,
    ) -> T:
        mark_active()
        try:
            result = call(*args, **kwargs)
            serialized = json.dumps(
                result, allow_nan=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if len(serialized) > max_response_bytes:
                raise WorkloadResponseTooLarge(
                    "workload response exceeds its byte ceiling"
                )
            return result
        except (TypeError, ValueError) as error:
            raise WorkloadError("workload result is not canonical JSON") from error
        finally:
            mark_inactive()

    async def run(
        self,
        call: Callable[..., T],
        *args,
        request_bytes: int,
        **kwargs,
    ) -> T:
        """Execute one admitted call and enforce canonical JSON output size."""
        if request_bytes < 0 or request_bytes > self.limits.max_request_bytes:
            self._increment("request_too_large")
            raise WorkloadRequestTooLarge("workload request exceeds its byte ceiling")
        if not self._slots.acquire(blocking=False):
            self._increment("saturated")
            raise WorkloadSaturated("workload admission queue is full")
        with self._lock:
            self._admitted += 1
            self._counters["accepted"] += 1

        def mark_active() -> None:
            with self._lock:
                self._active += 1

        def mark_inactive() -> None:
            with self._lock:
                self._active -= 1

        try:
            concurrent = self._executor.submit(
                self._invoke,
                call,
                args,
                kwargs,
                mark_active,
                mark_inactive,
                self.limits.max_response_bytes,
            )
        except Exception:
            with self._lock:
                self._admitted -= 1
                self._counters["failed"] += 1
            self._slots.release()
            raise
        concurrent.add_done_callback(self._release)
        wrapped = asyncio.wrap_future(concurrent)
        try:
            result = await asyncio.wait_for(
                asyncio.shield(wrapped), timeout=self.limits.timeout_seconds
            )
        except TimeoutError as error:
            concurrent.cancel()
            self._increment("timed_out")
            raise WorkloadTimedOut("workload execution timed out") from error
        except asyncio.CancelledError:
            concurrent.cancel()
            self._increment("cancelled")
            raise
        except WorkloadResponseTooLarge:
            self._increment("response_too_large")
            raise
        except Exception:
            self._increment("failed")
            raise

        self._increment("completed")
        return result

    def snapshot(self) -> dict[str, int | float]:
        """Return bounded non-sensitive limits, gauges, and failure counters."""
        with self._lock:
            return {
                "max_concurrency": self.limits.max_concurrency,
                "max_queue_depth": self.limits.max_queue_depth,
                "timeout_seconds": self.limits.timeout_seconds,
                "max_request_bytes": self.limits.max_request_bytes,
                "max_response_bytes": self.limits.max_response_bytes,
                "active": self._active,
                "admitted": self._admitted,
                **self._counters,
            }
