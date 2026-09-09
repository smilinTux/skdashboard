"""Bounded execution for read-only SKRSI visibility providers."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class VisibilityUnavailable(RuntimeError):
    def __init__(self, evidence: dict[str, Any], status_code: int = 503) -> None:
        super().__init__(evidence["code"])
        self.evidence = evidence
        self.status_code = status_code


class VisibilityProviderBoundary:
    """Run providers behind fixed worker, queue, retry, and timeout bounds."""

    def __init__(
        self,
        home: Path,
        *,
        timeout_seconds: float = 1.0,
        max_concurrency: int = 2,
        max_queue: int = 2,
        retries: int = 1,
        notifier: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_concurrency < 1 or max_queue < 0 or retries < 0:
            raise ValueError("visibility execution bounds must be non-negative")
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._slots = threading.BoundedSemaphore(max_concurrency + max_queue)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency, thread_name_prefix="skrsi-visibility"
        )
        self._evidence_path = home / "evidence" / "skrsi-visibility" / "terminal.jsonl"
        self._notifier = notifier
        self._write_lock = threading.Lock()

    def _failure(
        self, kind: str, code: str, attempts: int, status_code: int = 503
    ) -> None:
        core = {
            "schema": "skdashboard.visibility.provider-terminal.v1",
            "kind": kind,
            "code": code,
            "attempts": attempts,
            "terminal": True,
            "escalation": "notification_only",
        }
        core["evidence_hash"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        record = {**core, "observed_at": datetime.now(timezone.utc).isoformat()}
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self._write_lock:
            self._evidence_path.parent.mkdir(parents=True, exist_ok=True)
            with self._evidence_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
        if self._notifier is not None:
            try:
                self._notifier(dict(core))
            except Exception:
                pass
        raise VisibilityUnavailable(core, status_code)

    def invoke(self, kind: str, provider: Callable[[str], Any]) -> Mapping[str, Any]:
        if not self._slots.acquire(blocking=False):
            self._failure(kind, "visibility_provider_overloaded", 0)
        future: Future[Any] | None = None
        release_now = True
        try:
            for attempt in range(1, self.retries + 2):
                future = self._executor.submit(provider, kind)
                try:
                    supplied = future.result(timeout=self.timeout_seconds)
                except TimeoutError:
                    release_now = False
                    future.add_done_callback(lambda _future: self._slots.release())
                    self._failure(kind, "visibility_provider_timeout", attempt)
                except Exception:
                    if attempt <= self.retries:
                        continue
                    self._failure(kind, "visibility_provider_failed", attempt)
                if not isinstance(supplied, Mapping):
                    self._failure(kind, "visibility_provider_malformed", attempt, 422)
                if supplied.get("freshness", "unknown") != "current":
                    self._failure(kind, "visibility_provider_stale", attempt)
                return supplied
            raise AssertionError("unreachable")
        finally:
            if release_now:
                self._slots.release()
