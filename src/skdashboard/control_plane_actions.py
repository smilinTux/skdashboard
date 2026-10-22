"""Bounded, append-only state for governed action previews and receipts."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Mapping


class ActionPreviewError(ValueError):
    """A preview or authorization request failed a deterministic guard."""


class IdempotencyConflict(ActionPreviewError):
    """An idempotency key was reused for a different preview hash."""


def canonical_preview_hash(preview: Mapping[str, object]) -> str:
    """Hash the exact preview fields other than the self-referential hash."""

    payload = {key: value for key, value in preview.items() if key != "preview_hash"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def normalize_preview(preview: Mapping[str, object]) -> dict[str, object]:
    """Copy a provider preview and require its hash to be exact when supplied."""

    candidate = copy.deepcopy(dict(preview))
    expected = canonical_preview_hash(candidate)
    supplied = candidate.get("preview_hash")
    if supplied is not None and supplied != expected:
        raise ActionPreviewError("preview hash does not match the exact preview")
    candidate["preview_hash"] = expected
    return candidate


class ActionPreviewStore:
    """Keep previews and idempotent receipts for one application lifetime.

    The store never edits an existing preview or receipt. A durable owner
    service remains responsible for its own persistent command and outcome
    records; this store is the bounded control-plane handoff ledger.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previews: dict[str, dict[str, object]] = {}
        self._receipts: dict[tuple[str, str], dict[str, object]] = {}
        self._inflight: dict[tuple[str, str], str] = {}
        self._events: list[dict[str, object]] = []

    def put_preview(self, preview: Mapping[str, object]) -> dict[str, object]:
        value = copy.deepcopy(dict(preview))
        preview_id = value.get("preview_id")
        if not isinstance(preview_id, str) or not preview_id:
            raise ActionPreviewError("preview id is required")
        with self._lock:
            prior = self._previews.get(preview_id)
            if prior is not None and prior != value:
                raise ActionPreviewError("preview id already has a different exact value")
            if prior is None:
                self._previews[preview_id] = value
                self._events.append({"kind": "preview_created", "preview": copy.deepcopy(value)})
            return copy.deepcopy(self._previews[preview_id])

    def get_preview(self, preview_id: str) -> dict[str, object] | None:
        with self._lock:
            value = self._previews.get(preview_id)
            return copy.deepcopy(value) if value is not None else None

    def find_receipt(self, subject: str, idempotency_key: str) -> dict[str, object] | None:
        with self._lock:
            value = self._receipts.get((subject, idempotency_key))
            return copy.deepcopy(value) if value is not None else None

    def reserve_receipt(
        self, subject: str, idempotency_key: str, preview_hash: str
    ) -> dict[str, object] | None:
        """Reserve one owner handoff, or return its already recorded receipt."""

        key = (subject, idempotency_key)
        with self._lock:
            prior = self._receipts.get(key)
            if prior is not None:
                if prior.get("preview_hash") != preview_hash:
                    raise IdempotencyConflict("idempotency key belongs to another preview")
                return copy.deepcopy(prior)
            in_flight = self._inflight.get(key)
            if in_flight is not None:
                if in_flight != preview_hash:
                    raise IdempotencyConflict("idempotency key belongs to another preview")
                raise ActionPreviewError("idempotency key is already in flight")
            self._inflight[key] = preview_hash
            return None

    def release_receipt(self, subject: str, idempotency_key: str, preview_hash: str) -> None:
        with self._lock:
            key = (subject, idempotency_key)
            if self._inflight.get(key) == preview_hash:
                del self._inflight[key]

    def record_receipt(
        self, subject: str, idempotency_key: str, receipt: Mapping[str, object]
    ) -> dict[str, object]:
        value = copy.deepcopy(dict(receipt))
        with self._lock:
            key = (subject, idempotency_key)
            prior = self._receipts.get(key)
            if prior is not None:
                if prior.get("preview_hash") != value.get("preview_hash"):
                    raise IdempotencyConflict("idempotency key belongs to another preview")
                return copy.deepcopy(prior)
            self._receipts[key] = value
            self._inflight.pop(key, None)
            self._events.append({"kind": "receipt_recorded", "receipt": copy.deepcopy(value)})
            return copy.deepcopy(value)

    def events(self) -> list[dict[str, object]]:
        with self._lock:
            return copy.deepcopy(self._events)


__all__ = [
    "ActionPreviewError",
    "ActionPreviewStore",
    "IdempotencyConflict",
    "canonical_preview_hash",
    "normalize_preview",
]
