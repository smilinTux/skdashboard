"""Governed SKCP-03 command rail.

The rail deliberately exposes a closed registry. Commands are dispatched to an
explicit owner adapter and never to a shell, URL, or arbitrary callable chosen
by a request. State and receipts are durable and append-only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class OwnerAdapter(Protocol):
    def preview(self, target: str, parameters: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def execute(self, target: str, parameters: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def rollback(self, target: str, receipt: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CommandSpec:
    name: str
    owner: str
    scope: str
    adapter: OwnerAdapter


class CommandRailError(RuntimeError):
    """A governed command was refused."""


class SQLiteCommandState:
    """Atomic idempotency records and immutable receipts."""
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS commands (
              key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status TEXT NOT NULL,
              receipt TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
              receipt TEXT NOT NULL, receipt_hash TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
              BEGIN SELECT RAISE(ABORT, 'audit is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
              BEGIN SELECT RAISE(ABORT, 'audit is immutable'); END;
            """)

    def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT request_hash,status,receipt FROM commands WHERE key=?", (key,)).fetchone()
        return None if row is None else (row[0], {**json.loads(row[2]), "status": row[1]})

    def save(self, key: str, request_hash: str, status: str, receipt: Mapping[str, Any]) -> None:
        encoded = _json(receipt)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO commands VALUES (?,?,?,?,?)", (key, request_hash, status, encoded, time.time()))
            db.execute("INSERT INTO audit(key,receipt,receipt_hash,created_at) VALUES(?,?,?,?)",
                       (key, encoded, hashlib.sha256(encoded.encode()).hexdigest(), time.time()))
            db.commit()


class CommandRail:
    """Closed, least-privilege command dispatcher."""
    def __init__(self, state: SQLiteCommandState, specs: tuple[CommandSpec, ...], current_version: Callable[[str], int]):
        names = [s.name for s in specs]
        if len(names) != len(set(names)) or any(not s.name or not s.owner or not s.scope for s in specs):
            raise ValueError("command registry must be closed and uniquely named")
        self._specs = {s.name: s for s in specs}
        self._state, self._version = state, current_version

    @property
    def registry(self) -> tuple[dict[str, str], ...]:
        return tuple({"name": s.name, "owner": s.owner, "scope": s.scope} for s in self._specs.values())

    def dispatch(self, *, command: str, target: str, parameters: Mapping[str, Any], actor: str,
                 idempotency_key: str, expected_version: int | None = None, preview: bool = False,
                 scope: str = "") -> dict[str, Any]:
        spec = self._specs.get(command)
        if spec is None:
            raise CommandRailError("command is not in the reviewed registry")
        if scope != spec.scope:
            raise CommandRailError("least-privilege scope denied")
        if not actor or not idempotency_key or not target:
            raise CommandRailError("actor, target, and idempotency key are required")
        request = {"command": command, "target": target, "parameters": dict(parameters), "actor": actor,
                   "expected_version": expected_version, "scope": scope}
        request_hash = _digest(request)
        if not preview:
            prior = self._state.get(idempotency_key)
            if prior:
                if prior[0] != request_hash:
                    raise CommandRailError("idempotency key reused for a different request")
                return prior[1]
            if expected_version is not None and expected_version != self._version(target):
                return {"status": "conflict", "expected_version": expected_version, "actual_version": self._version(target)}
        result = spec.adapter.preview(target, parameters) if preview else spec.adapter.execute(target, parameters)
        receipt = {"command": command, "owner": spec.owner, "target": target, "actor": actor,
                   "result": dict(result), "request_hash": request_hash, "preview": preview}
        if not preview:
            receipt["version"] = self._version(target)
            try:
                self._state.save(idempotency_key, request_hash, "executed", receipt)
            except sqlite3.IntegrityError:
                prior = self._state.get(idempotency_key)
                if prior and prior[0] == request_hash:
                    return prior[1]
                raise CommandRailError("atomic idempotency race")
        return {"status": "preview" if preview else "executed", **receipt}

    def rollback(self, *, command: str, target: str, receipt: Mapping[str, Any], actor: str, scope: str) -> Mapping[str, Any]:
        spec = self._specs.get(command)
        if spec is None or scope != (spec.scope + ".rollback"):
            raise CommandRailError("rollback scope denied")
        return spec.adapter.rollback(target, receipt)
