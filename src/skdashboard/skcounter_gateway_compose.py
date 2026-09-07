"""Compose a transported SKCounter gateway index into the dashboard data root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from .dashboard_skcounter import (
    INDEX_SCHEMA_VERSION,
    MAX_INDEX_ENTRIES,
    MAX_OBSERVATION_BYTES,
    SnapshotError,
    _canonical_json,
    _normalize_snapshot,
    _read_index_generation,
)

PRODUCER_FIELDS = frozenset(
    {
        "schema_version",
        "key",
        "measurement_lane",
        "node_id",
        "principal_id",
        "view",
        "bucket_start",
        "observation_path",
        "observed_at",
        "payload_hash",
        "idempotency_key",
    }
)


def _source_path(root: Path, value: Any) -> tuple[PurePosixPath, Path]:
    if not isinstance(value, str):
        raise SnapshotError("producer observation path is invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise SnapshotError("producer observation path is invalid")
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
        raise SnapshotError("producer observation is unavailable")
    return relative, path


def _read_producer(index_path: Path, sent_root: Path) -> tuple[list[dict], dict[str, bytes]]:
    if index_path.is_symlink() or not index_path.is_file():
        raise SnapshotError("producer index is unavailable")
    entries: list[dict] = []
    sources: dict[str, bytes] = {}
    seen: set[tuple[str, str, str, str, str]] = set()
    for position, line in enumerate(index_path.read_text(encoding="utf-8").splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"producer index entry {position} is malformed") from exc
        if not isinstance(entry, dict) or set(entry) != PRODUCER_FIELDS:
            raise SnapshotError(f"producer index entry {position} fields do not match v1")
        if (
            entry["schema_version"] != INDEX_SCHEMA_VERSION
            or entry["measurement_lane"] != "gateway_observed"
        ):
            raise SnapshotError(f"producer index entry {position} is not gateway_observed v1")
        key = tuple(
            entry[field]
            for field in ("measurement_lane", "node_id", "principal_id", "view", "bucket_start")
        )
        if key in seen:
            raise SnapshotError("producer index contains duplicate keys")
        seen.add(key)
        relative, source = _source_path(sent_root, entry["observation_path"])
        raw = source.read_bytes()
        if not 1 < len(raw) <= MAX_OBSERVATION_BYTES:
            raise SnapshotError("producer observation size is invalid")
        try:
            observation = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SnapshotError("producer observation is malformed") from exc
        meta, rows = _normalize_snapshot(observation)
        payload = {
            key: value
            for key, value in observation.items()
            if key not in {"idempotency_key", "payload_hash"}
        }
        payload_hash = hashlib.sha256(_canonical_json(payload)).hexdigest()
        if (
            meta["lane"] != "gateway_observed"
            or meta["node_id"] != entry["node_id"]
            or meta["principal_id"] != entry["principal_id"]
            or observation["observed_at"] != entry["observed_at"]
            or observation["payload_hash"] != entry["payload_hash"]
            or observation["idempotency_key"] != entry["idempotency_key"]
            or observation["payload_hash"] != payload_hash
            or observation["idempotency_key"] != payload_hash
            or not any(
                row["view"] == entry["view"]
                and row["bucket_start"].isoformat().replace("+00:00", "Z") == entry["bucket_start"]
                for row in rows
            )
        ):
            raise SnapshotError("producer index provenance does not match its observation")
        source_sha256 = hashlib.sha256(raw).hexdigest()
        destination = f"observations/gateway/{relative.as_posix()}"
        entries.append(
            {
                "measurement_lane": "gateway_observed",
                "node_id": entry["node_id"],
                "principal_id": entry["principal_id"],
                "view": entry["view"],
                "bucket_start": entry["bucket_start"],
                "observed_at": entry["observed_at"],
                "idempotency_key": entry["idempotency_key"],
                "payload_hash": entry["payload_hash"],
                "source_path": destination,
                "source_sha256": source_sha256,
            }
        )
        sources[destination] = raw
    if not entries or len(entries) > MAX_INDEX_ENTRIES:
        raise SnapshotError("producer index entry count is invalid")
    return entries, sources


def compose(index_path: Path, sent_root: Path, destination_root: Path) -> dict[str, Any]:
    """Validate and atomically publish one transported gateway generation."""
    gateway_entries, sources = _read_producer(index_path, sent_root.resolve())
    current = _read_index_generation(destination_root)
    retained = (
        []
        if current is None
        else [
            entry
            for entry in current[0]["entries"]
            if entry["measurement_lane"] != "gateway_observed"
        ]
    )
    entries = sorted(
        [*retained, *gateway_entries],
        key=lambda item: tuple(
            item[field]
            for field in ("measurement_lane", "node_id", "principal_id", "view", "bucket_start")
        ),
    )
    if len(entries) > MAX_INDEX_ENTRIES:
        raise SnapshotError("composed index exceeds its entry limit")
    for relative, raw in sources.items():
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            if destination.read_bytes() != raw:
                raise SnapshotError("immutable destination observation conflicts")
            continue
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_bytes(raw)
        temporary.chmod(0o400)
        os.replace(temporary, destination)
    unsigned = {"schema_version": INDEX_SCHEMA_VERSION, "entries": entries}
    document = {**unsigned, "index_sha256": hashlib.sha256(_canonical_json(unsigned)).hexdigest()}
    index_destination = destination_root / "observation-index" / "latest.json"
    index_destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = index_destination.with_name(f".{index_destination.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical_json(document) + b"\n")
    temporary.chmod(0o400)
    os.replace(temporary, index_destination)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--producer-index", required=True, type=Path)
    parser.add_argument("--producer-sent-root", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = compose(args.producer_index, args.producer_sent_root, args.destination_root)
    except (OSError, UnicodeError, SnapshotError) as exc:
        parser.error(str(exc))
    print(
        json.dumps({"entries": len(document["entries"]), "index_sha256": document["index_sha256"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
