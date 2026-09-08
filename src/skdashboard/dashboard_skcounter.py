"""Read-only SKCounter projections for the Economy workspace.

The dashboard reads validated aggregate observations from the central
SKCounter data root. It never scans coding-harness session stores and never
accepts raw prompt or response material.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .node_coverage import node_coverage

SCHEMA_VERSION = "skcounter.snapshot.v1"
LANES = frozenset({"harness_reported", "gateway_observed"})
VIEWS = frozenset(
    {
        "models",
        "daily",
        "hourly",
        "agents",
        "workspaces",
        "sessions",
        "tasks",
        "time_metrics",
    }
)
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning", "total")
MAX_OBSERVATION_BYTES = 5 * 1024 * 1024
MAX_INDEX_BYTES = 10 * 1024 * 1024
MAX_INDEX_ENTRIES = 10_000
MAX_INDEX_SOURCES = MAX_INDEX_ENTRIES
MAX_TOTAL_OBSERVATION_BYTES = 64 * 1024 * 1024
MAX_INDEX_READ_ATTEMPTS = 3
MAX_REPORTED_ERRORS = 100
FRESH_SECONDS = 45 * 60
DELAYED_SECONDS = 24 * 60 * 60
INDEX_SCHEMA_VERSION = "skcounter.latest-observation-index.v1"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

_INDEX_ENTRY_FIELDS = frozenset(
    {
        "measurement_lane",
        "node_id",
        "principal_id",
        "view",
        "bucket_start",
        "observed_at",
        "idempotency_key",
        "payload_hash",
        "source_path",
        "source_sha256",
    }
)

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "idempotency_key",
        "measurement_lane",
        "node_id",
        "principal_id",
        "collector",
        "observed_at",
        "bucket_timezone",
        "window",
        "source_state_digest",
        "aggregates",
        "payload_hash",
    }
)
_AGGREGATE_FIELDS = frozenset(
    {
        "view",
        "bucket_start",
        "client",
        "provider",
        "model",
        "agent",
        "workspace_key",
        "workspace_label",
        "session_key",
        "task_label",
        "tokens",
        "message_count",
        "cost",
        "performance",
        "activity",
    }
)
_PROHIBITED_FIELDS = frozenset(
    {
        "prompt",
        "response",
        "content",
        "tool_input",
        "tool_output",
        "workspace_path",
        "source_path",
        "sessions_path",
        "session_id",
        "credential",
        "capability_token",
        "api_key",
        "cookie",
        "oauth_token",
    }
)


class SnapshotError(ValueError):
    """A snapshot cannot enter the dashboard read model."""


def _data_root(home: Path) -> Path:
    configured = os.environ.get("SKCOUNTER_DATA_DIR")
    return Path(configured).expanduser() if configured else Path(home).expanduser() / "skcounter"


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise SnapshotError(f"{field} must be a date-time string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"{field} is not a valid date-time") from exc
    if parsed.tzinfo is None:
        raise SnapshotError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, field: str, maximum: int = 256, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SnapshotError(f"{field} must be a non-empty bounded string")
    return value


def _number(value: Any, field: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SnapshotError(f"{field} must be numeric")
    if not math.isfinite(float(value)) or value < 0:
        raise SnapshotError(f"{field} must be finite and non-negative")
    if integer and not isinstance(value, int):
        raise SnapshotError(f"{field} must be an integer")
    return value


def _exact_fields(value: dict, allowed: frozenset[str], field: str) -> None:
    extras = set(value) - allowed
    if extras:
        raise SnapshotError(f"{field} contains unsupported fields")


def _reject_prohibited(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _PROHIBITED_FIELDS:
                raise SnapshotError("snapshot contains a prohibited raw-data field")
            _reject_prohibited(child)
    elif isinstance(value, list):
        for child in value:
            _reject_prohibited(child)


def _optional_hash(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SnapshotError(f"{field} must be a SHA-256 hex digest")
    return value


def _validate_tokens(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(TOKEN_FIELDS):
        raise SnapshotError("tokens must contain the exact v1 token fields")
    return {field: int(_number(value[field], f"tokens.{field}", integer=True)) for field in TOKEN_FIELDS}


def _validate_cost(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SnapshotError("cost must be an object")
    expected = {"amount", "currency", "estimated", "pricing_revision"}
    if set(value) != expected or value.get("currency") != "USD":
        raise SnapshotError("cost must use the exact v1 USD fields")
    if not isinstance(value.get("estimated"), bool):
        raise SnapshotError("cost.estimated must be boolean")
    return {
        "amount": float(_number(value["amount"], "cost.amount")),
        "estimated": value["estimated"],
        "pricing_revision": _text(value["pricing_revision"], "cost.pricing_revision"),
    }


def _validate_performance(value: Any) -> dict[str, Any]:
    if value is None:
        return {
            "duration_ms": 0,
            "timed_tokens": 0,
            "sample_count": 0,
            "token_coverage": 0.0,
            "ms_per_1k_tokens": None,
        }
    if not isinstance(value, dict):
        raise SnapshotError("performance must be an object")
    required = {"duration_ms", "timed_tokens", "sample_count", "token_coverage"}
    allowed = required | {"ms_per_1k_tokens"}
    if not required.issubset(value) or set(value) - allowed:
        raise SnapshotError("performance fields do not match v1")
    coverage = float(_number(value["token_coverage"], "performance.token_coverage"))
    if coverage > 1:
        raise SnapshotError("performance.token_coverage cannot exceed one")
    speed = value.get("ms_per_1k_tokens")
    if speed is not None:
        speed = float(_number(speed, "performance.ms_per_1k_tokens"))
    return {
        "duration_ms": int(_number(value["duration_ms"], "performance.duration_ms", integer=True)),
        "timed_tokens": int(_number(value["timed_tokens"], "performance.timed_tokens", integer=True)),
        "sample_count": int(_number(value["sample_count"], "performance.sample_count", integer=True)),
        "token_coverage": coverage,
        "ms_per_1k_tokens": speed,
    }


def _validate_activity(value: Any) -> dict[str, int]:
    empty = {"active_seconds": 0, "longest_continuous_seconds": 0, "max_concurrent": 0}
    if value is None:
        return empty
    if not isinstance(value, dict) or set(value) != set(empty):
        raise SnapshotError("activity fields do not match v1")
    return {
        field: int(_number(value[field], f"activity.{field}", integer=True)) for field in empty
    }


def _normalize_snapshot(document: Any) -> tuple[dict, list[dict]]:
    if not isinstance(document, dict):
        raise SnapshotError("snapshot must be an object")
    _reject_prohibited(document)
    _exact_fields(document, _TOP_LEVEL_FIELDS, "snapshot")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError("unsupported schema version")

    for digest_field in ("idempotency_key", "source_state_digest", "payload_hash"):
        if not _SHA256_RE.fullmatch(str(document.get(digest_field, ""))):
            raise SnapshotError(f"{digest_field} must be a SHA-256 hex digest")

    lane = document.get("measurement_lane")
    if lane not in LANES:
        raise SnapshotError("unsupported measurement lane")
    observed = _parse_time(document.get("observed_at"), "observed_at")
    node_id = _text(document.get("node_id"), "node_id", 128)
    principal_id = _text(document.get("principal_id"), "principal_id", 128)
    bucket_timezone = _text(document.get("bucket_timezone"), "bucket_timezone", 64)

    window = document.get("window")
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise SnapshotError("window fields do not match v1")
    window_start = _parse_time(window.get("start"), "window.start")
    window_end = _parse_time(window.get("end"), "window.end")
    if window_start > window_end:
        raise SnapshotError("window.start cannot be after window.end")

    collector = document.get("collector")
    if not isinstance(collector, dict):
        raise SnapshotError("collector must be an object")
    if set(collector) != {"product", "facade_version", "backend", "backend_version"}:
        raise SnapshotError("collector fields do not match v1")
    if collector.get("product") != "skcounter":
        raise SnapshotError("collector product must be skcounter")
    normalized_collector = {
        "facade_version": _text(collector.get("facade_version"), "collector.facade_version", 64),
        "backend": _text(collector.get("backend"), "collector.backend", 64),
        "backend_version": _text(
            collector.get("backend_version"), "collector.backend_version", 64
        ),
    }

    aggregates = document.get("aggregates")
    if not isinstance(aggregates, list) or len(aggregates) > 10_000:
        raise SnapshotError("aggregates must be a bounded array")

    meta = {
        "lane": lane,
        "node_id": node_id,
        "principal_id": principal_id,
        "observed_at": observed,
        "observed_at_text": observed.isoformat().replace("+00:00", "Z"),
        "bucket_timezone": bucket_timezone,
        "window_start": window_start,
        "window_end": window_end,
        **normalized_collector,
    }
    rows: list[dict] = []
    for index, aggregate in enumerate(aggregates):
        if not isinstance(aggregate, dict):
            raise SnapshotError(f"aggregate {index} must be an object")
        _exact_fields(aggregate, _AGGREGATE_FIELDS, f"aggregate {index}")
        view = aggregate.get("view")
        if view not in VIEWS:
            raise SnapshotError(f"aggregate {index} has unsupported view")
        bucket = _parse_time(aggregate.get("bucket_start"), f"aggregate {index}.bucket_start")
        rows.append(
            {
                **meta,
                "view": view,
                "bucket_start": bucket,
                "bucket_start_text": bucket.isoformat().replace("+00:00", "Z"),
                "client": _text(aggregate.get("client"), "client", 128, optional=True),
                "provider": _text(aggregate.get("provider"), "provider", 128, optional=True),
                "model": _text(aggregate.get("model"), "model", 256, optional=True),
                "agent": _text(aggregate.get("agent"), "agent", 128, optional=True),
                "workspace_key": _optional_hash(aggregate.get("workspace_key"), "workspace_key"),
                "workspace_label": _text(
                    aggregate.get("workspace_label"), "workspace_label", 128, optional=True
                ),
                "session_key": _optional_hash(aggregate.get("session_key"), "session_key"),
                "task_label": _text(
                    aggregate.get("task_label"), "task_label", 160, optional=True
                ),
                "tokens": _validate_tokens(aggregate.get("tokens")),
                "message_count": int(
                    _number(aggregate.get("message_count", 0), "message_count", integer=True)
                ),
                "cost": _validate_cost(aggregate.get("cost")),
                "performance": _validate_performance(aggregate.get("performance")),
                "activity": _validate_activity(aggregate.get("activity")),
            }
        )
    return meta, rows


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_stable_file(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise SnapshotError("file is not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(data) > maximum:
        raise SnapshotError("file exceeds its byte limit")
    before_state = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_state = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_state != after_state or len(data) != after.st_size:
        raise SnapshotError("file changed during read")
    return data


def _safe_source_path(root: Path, source_path: Any) -> tuple[str, Path]:
    value = _text(source_path, "index source_path", 1024)
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or "\\" in value
        or not relative.parts
        or relative.parts[0] != "observations"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise SnapshotError("index source_path is outside the observation store")
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*relative.parts)
    current = resolved_root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise SnapshotError("index source_path contains a symbolic link")
    return relative.as_posix(), candidate


def _index_logical_key(entry: dict) -> tuple[str, str, str, str, str]:
    return (
        entry["measurement_lane"],
        entry["node_id"],
        entry["principal_id"],
        entry["view"],
        entry["bucket_start"],
    )


def _validate_index_entry(entry: Any, position: int) -> dict:
    if not isinstance(entry, dict) or set(entry) != set(_INDEX_ENTRY_FIELDS):
        raise SnapshotError(f"index entry {position} fields do not match v1")
    normalized = {
        "measurement_lane": _text(entry.get("measurement_lane"), "measurement_lane", 64),
        "node_id": _text(entry.get("node_id"), "node_id", 128),
        "principal_id": _text(entry.get("principal_id"), "principal_id", 128),
        "view": _text(entry.get("view"), "view", 64),
        "bucket_start": _text(entry.get("bucket_start"), "bucket_start", 64),
        "observed_at": _text(entry.get("observed_at"), "observed_at", 64),
        "idempotency_key": _text(entry.get("idempotency_key"), "idempotency_key", 64),
        "payload_hash": _text(entry.get("payload_hash"), "payload_hash", 64),
        "source_path": _text(entry.get("source_path"), "source_path", 1024),
        "source_sha256": _text(entry.get("source_sha256"), "source_sha256", 64),
    }
    if normalized["measurement_lane"] not in LANES or normalized["view"] not in VIEWS:
        raise SnapshotError(f"index entry {position} has an unsupported lane or view")
    _parse_time(normalized["bucket_start"], "index bucket_start")
    _parse_time(normalized["observed_at"], "index observed_at")
    for field in ("idempotency_key", "payload_hash", "source_sha256"):
        if not _SHA256_RE.fullmatch(normalized[field]):
            raise SnapshotError(f"index entry {position} {field} is not a SHA-256 digest")
    return normalized


def _read_index_generation(root: Path) -> tuple[dict, bytes] | None:
    path = root / "observation-index" / "latest.json"
    try:
        raw = _read_stable_file(path, MAX_INDEX_BYTES)
    except FileNotFoundError:
        return None
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotError("latest observation index is malformed") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "entries", "index_sha256"}
        or document.get("schema_version") != INDEX_SCHEMA_VERSION
        or not isinstance(document.get("entries"), list)
        or len(document["entries"]) > MAX_INDEX_ENTRIES
        or not _SHA256_RE.fullmatch(str(document.get("index_sha256", "")))
    ):
        raise SnapshotError("latest observation index fields do not match v1")
    unsigned = {
        "schema_version": document["schema_version"],
        "entries": document["entries"],
    }
    expected = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if expected != document["index_sha256"]:
        raise SnapshotError("latest observation index hash mismatch")
    entries: list[dict] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for position, value in enumerate(document["entries"]):
        entry = _validate_index_entry(value, position)
        key = _index_logical_key(entry)
        if key in seen:
            raise SnapshotError("latest observation index contains duplicate keys")
        seen.add(key)
        entries.append(entry)
    if len({entry["source_path"] for entry in entries}) > MAX_INDEX_SOURCES:
        raise SnapshotError("latest observation index exceeds its source limit")
    return {**document, "entries": entries}, raw


def _read_indexed_sources(root: Path, entries: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        grouped[entry["source_path"]].append(entry)

    rows: list[dict] = []
    observations: list[dict] = []
    errors: list[str] = []
    total_bytes = 0
    for source_name in sorted(grouped):
        indexed = grouped[source_name]
        try:
            relative, path = _safe_source_path(root, source_name)
            source_hashes = {entry["source_sha256"] for entry in indexed}
            if len(source_hashes) != 1:
                raise SnapshotError("index disagrees on the source hash")
            raw = _read_stable_file(path, MAX_OBSERVATION_BYTES)
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_OBSERVATION_BYTES:
                raise SnapshotError("indexed observation byte budget exceeded")
            source_sha256 = hashlib.sha256(raw).hexdigest()
            if source_sha256 not in source_hashes:
                raise SnapshotError("indexed observation hash mismatch")
            try:
                document = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SnapshotError("indexed observation is malformed") from exc
            meta, normalized_rows = _normalize_snapshot(document)
            selected_rows: list[dict] = []
            for entry in indexed:
                if (
                    entry["measurement_lane"] != meta["lane"]
                    or entry["node_id"] != meta["node_id"]
                    or entry["principal_id"] != meta["principal_id"]
                    or _parse_time(entry["observed_at"], "index observed_at")
                    != meta["observed_at"]
                    or entry["payload_hash"] != document["payload_hash"]
                    or entry["idempotency_key"] != document["idempotency_key"]
                ):
                    raise SnapshotError("index provenance does not match its observation")
                bucket = _parse_time(entry["bucket_start"], "index bucket_start")
                matching = [
                    row
                    for row in normalized_rows
                    if row["view"] == entry["view"] and row["bucket_start"] == bucket
                ]
                if not matching:
                    raise SnapshotError("index key does not exist in its observation")
                selected_rows.extend(matching)
            observations.append(
                {
                    **meta,
                    "source_path": relative,
                    "source_sha256": source_sha256,
                    "payload_hash": document["payload_hash"],
                    "idempotency_key": document["idempotency_key"],
                    "source_bytes": len(raw),
                }
            )
            rows.extend(selected_rows)
        except (OSError, SnapshotError) as exc:
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"{source_name}: {exc}")
    return observations, rows, errors


def _read_observations(
    home: Path,
) -> tuple[list[dict], list[dict], list[str], dict[str, Any]]:
    root = _data_root(home)
    for _attempt in range(MAX_INDEX_READ_ATTEMPTS):
        try:
            generation = _read_index_generation(root)
        except (OSError, SnapshotError) as exc:
            return [], [], [str(exc)], {
                "schema_version": INDEX_SCHEMA_VERSION,
                "status": "unavailable",
                "index_sha256": "",
                "entry_count": 0,
                "source_count": 0,
            }
        if generation is None:
            observations, rows, errors = _read_acknowledged_snapshots(root)
            if observations or rows or errors:
                return observations, rows, errors, {
                    "schema_version": INDEX_SCHEMA_VERSION,
                    "status": "partial" if errors else "current",
                    "index_sha256": "",
                    "entry_count": len(rows),
                    "source_count": len(observations),
                }
            return [], [], ["latest observation index is unavailable"], {
                "schema_version": INDEX_SCHEMA_VERSION,
                "status": "unavailable",
                "index_sha256": "",
                "entry_count": 0,
                "source_count": 0,
            }
        document, raw = generation
        observations, rows, errors = _read_indexed_sources(root, document["entries"])
        try:
            confirmed = _read_index_generation(root)
        except (OSError, SnapshotError):
            continue
        if confirmed is None or confirmed[1] != raw:
            continue
        return observations, rows, errors, {
            "schema_version": INDEX_SCHEMA_VERSION,
            "status": "partial" if errors else ("current" if document["entries"] else "empty"),
            "index_sha256": document["index_sha256"],
            "entry_count": len(document["entries"]),
            "source_count": len({entry["source_path"] for entry in document["entries"]}),
        }
    return [], [], ["latest observation index changed during bounded read"], {
        "schema_version": INDEX_SCHEMA_VERSION,
        "status": "changing",
        "index_sha256": "",
        "entry_count": 0,
        "source_count": 0,
    }


def _read_acknowledged_snapshots(root: Path) -> tuple[list[dict], list[dict], list[str]]:
    """Read only the latest acknowledged snapshot per node and principal."""
    sent_root = root / "sent"
    paths: list[Path] = []
    if sent_root.is_dir():
        for principal_root in sorted(sent_root.glob("*/*"))[:MAX_INDEX_SOURCES]:
            if principal_root.is_dir() and not principal_root.is_symlink():
                latest = max(
                    principal_root.glob("*.json"),
                    key=lambda path: path.stat().st_mtime_ns,
                    default=None,
                )
                if latest is not None:
                    paths.append(latest)

    observations: list[dict] = []
    rows: list[dict] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > MAX_OBSERVATION_BYTES:
                raise SnapshotError("unsafe link or oversized observation")
            raw = path.read_bytes()
            document = json.loads(raw.decode("utf-8"))
            meta, normalized_rows = _normalize_snapshot(document)
            identity = (
                document["idempotency_key"],
                meta["lane"],
                meta["node_id"],
                meta["principal_id"],
                meta["observed_at_text"],
            )
            if identity in seen:
                continue
            seen.add(identity)
            observations.append(
                {
                    **meta,
                    "source_path": str(path.relative_to(root)),
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "source_bytes": len(raw),
                    "payload_hash": document["payload_hash"],
                    "idempotency_key": document["idempotency_key"],
                }
            )
            rows.extend(normalized_rows)
        except (OSError, UnicodeError, json.JSONDecodeError, SnapshotError) as exc:
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"{path.name}: {exc}")
    return observations, rows, errors


def _latest_rows(rows: Iterable[dict]) -> list[dict]:
    latest: dict[tuple, dict] = {}
    for row in rows:
        key = (
            row["lane"],
            row["view"],
            row["node_id"],
            row["principal_id"],
            row["bucket_start_text"],
            row["client"],
            row["provider"],
            row["model"],
            row["agent"],
            row["workspace_key"],
            row["session_key"],
        )
        prior = latest.get(key)
        if prior is None or row["observed_at"] > prior["observed_at"]:
            latest[key] = row
    return list(latest.values())


def _empty_totals() -> dict[str, Any]:
    return {
        "tokens": {field: 0 for field in TOKEN_FIELDS},
        "message_count": 0,
        "cost_usd": 0.0,
        "cost_state": "unavailable",
        "pricing_revisions": [],
        "cache_ratio": 0.0,
        "duration_ms": 0,
        "timed_tokens": 0,
        "sample_count": 0,
        "token_coverage": 0.0,
        "ms_per_1k_tokens": None,
        "active_seconds": 0,
        "longest_continuous_seconds": 0,
        "max_concurrent": 0,
    }


def _summarize(rows: Iterable[dict]) -> dict[str, Any]:
    rows = list(rows)
    result = _empty_totals()
    cost_flags: list[bool] = []
    revisions: set[str] = set()
    for row in rows:
        for field in TOKEN_FIELDS:
            result["tokens"][field] += row["tokens"][field]
        result["message_count"] += row["message_count"]
        result["duration_ms"] += row["performance"]["duration_ms"]
        result["timed_tokens"] += row["performance"]["timed_tokens"]
        result["sample_count"] += row["performance"]["sample_count"]
        result["active_seconds"] += row["activity"]["active_seconds"]
        result["longest_continuous_seconds"] = max(
            result["longest_continuous_seconds"],
            row["activity"]["longest_continuous_seconds"],
        )
        result["max_concurrent"] = max(
            result["max_concurrent"], row["activity"]["max_concurrent"]
        )
        if row["cost"] is not None:
            result["cost_usd"] += row["cost"]["amount"]
            cost_flags.append(row["cost"]["estimated"])
            revisions.add(row["cost"]["pricing_revision"])

    input_context = result["tokens"]["input"] + result["tokens"]["cache_read"]
    result["cache_ratio"] = (
        result["tokens"]["cache_read"] / input_context if input_context else 0.0
    )
    total = result["tokens"]["total"]
    result["token_coverage"] = min(1.0, result["timed_tokens"] / total) if total else 0.0
    result["ms_per_1k_tokens"] = (
        result["duration_ms"] / result["timed_tokens"] * 1000
        if result["timed_tokens"]
        else None
    )
    result["cost_usd"] = round(result["cost_usd"], 8)
    result["pricing_revisions"] = sorted(revisions)
    if cost_flags:
        if all(cost_flags):
            result["cost_state"] = "estimated"
        elif not any(cost_flags):
            result["cost_state"] = "billed"
        else:
            result["cost_state"] = "mixed"
    return result


def _breakdown(rows: Iterable[dict], dimension: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        value = row.get(dimension) or "unknown"
        grouped[value].append(row)
    result = [{dimension: key, **_summarize(value)} for key, value in grouped.items()]
    return sorted(result, key=lambda item: (-item["tokens"]["total"], item[dimension]))


def _private_breakdown(rows: Iterable[dict], dimension: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if dimension == "workspace":
            value = row["workspace_label"] or (
                f"workspace {row['workspace_key'][:8]}" if row["workspace_key"] else "unknown"
            )
        elif dimension == "session":
            value = f"session {row['session_key'][:8]}" if row["session_key"] else "unknown"
        else:
            value = row["task_label"] or "unknown"
        grouped[value].append(row)
    result = [{dimension: key, **_summarize(value)} for key, value in grouped.items()]
    return sorted(result, key=lambda item: (-item["tokens"]["total"], item[dimension]))


def _series(rows: Iterable[dict], granularity: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = row["bucket_start_text"][:13] if granularity == "hour" else row["bucket_start_text"][:10]
        grouped[key].append(row)
    return [
        {"bucket": key, **_summarize(grouped[key])}
        for key in sorted(grouped)
    ]


def _collector_status(age_seconds: int) -> str:
    if age_seconds <= FRESH_SECONDS:
        return "fresh"
    if age_seconds <= DELAYED_SECONDS:
        return "delayed"
    return "stale"


def _collectors(observations: Iterable[dict], lane: str, now: datetime) -> list[dict]:
    latest: dict[tuple[str, str], dict] = {}
    for observation in observations:
        if observation["lane"] != lane:
            continue
        key = (observation["node_id"], observation["principal_id"])
        if key not in latest or observation["observed_at"] > latest[key]["observed_at"]:
            latest[key] = observation

    result = []
    for observation in latest.values():
        age = max(0, int((now - observation["observed_at"]).total_seconds()))
        result.append(
            {
                "node_id": observation["node_id"],
                "principal_id": observation["principal_id"],
                "facade_version": observation["facade_version"],
                "backend": observation["backend"],
                "backend_version": observation["backend_version"],
                "last_seen": observation["observed_at_text"],
                "age_seconds": age,
                "status": _collector_status(age),
            }
        )
    rank = {"fresh": 0, "delayed": 1, "stale": 2}
    return sorted(
        result,
        key=lambda item: (rank[item["status"]], item["node_id"], item["principal_id"]),
    )


def _apply_filters(rows: Iterable[dict], filters: dict[str, str]) -> list[dict]:
    result = []
    start = filters.get("from", "")
    end = filters.get("to", "")
    for row in rows:
        if filters.get("node") and row["node_id"] != filters["node"]:
            continue
        if filters.get("client") and row["client"] != filters["client"]:
            continue
        if filters.get("provider") and row["provider"] != filters["provider"]:
            continue
        if filters.get("model") and row["model"] != filters["model"]:
            continue
        day = row["bucket_start_text"][:10]
        if start and day < start:
            continue
        if end and day > end:
            continue
        result.append(row)
    return result


def _expected_nodes(lane: str) -> list[str]:
    environment = (
        "SKCOUNTER_EXPECTED_GATEWAY_NODES"
        if lane == "gateway_observed"
        else "SKCOUNTER_EXPECTED_NODES"
    )
    return sorted(
        {
            value.strip()
            for value in os.environ.get(environment, "").split(",")
            if value.strip()
        }
    )


def get_ai_usage(
    home: Path,
    filters: dict[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Project append-only SKCounter observations into a dashboard read model."""

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    filters = {key: str(value) for key, value in (filters or {}).items() if value}
    observations, rows, errors, index = _read_observations(home)
    requested_lane = filters.get("lane", "harness_reported")
    lane = requested_lane if requested_lane in LANES else "harness_reported"
    if index["status"] in {"current", "partial", "empty"} and not any(
        observation["lane"] == lane for observation in observations
    ):
        fallback_observations, fallback_rows, fallback_errors = _read_acknowledged_snapshots(
            _data_root(home)
        )
        observations.extend(
            observation for observation in fallback_observations if observation["lane"] == lane
        )
        rows.extend(row for row in fallback_rows if row["lane"] == lane)
        errors.extend(fallback_errors)
    rows = _latest_rows(rows)
    available_lanes = sorted({row["lane"] for row in rows})
    lane_rows = [row for row in rows if row["lane"] == lane]
    model_rows_all = [row for row in lane_rows if row["view"] == "models"]
    facets = {
        "nodes": sorted({row["node_id"] for row in model_rows_all}),
        "clients": sorted({row["client"] for row in model_rows_all if row["client"]}),
        "providers": sorted({row["provider"] for row in model_rows_all if row["provider"]}),
        "models": sorted({row["model"] for row in model_rows_all if row["model"]}),
    }

    filtered = _apply_filters(lane_rows, filters)
    models = [row for row in filtered if row["view"] == "models"]
    daily = [row for row in filtered if row["view"] == "daily"]
    hourly = [row for row in filtered if row["view"] == "hourly"]
    agents = [row for row in filtered if row["view"] == "agents"]
    workspaces = [row for row in filtered if row["view"] == "workspaces"]
    sessions = [row for row in filtered if row["view"] == "sessions"]
    tasks = [row for row in filtered if row["view"] == "tasks"]
    activity_rows = [row for row in filtered if row["view"] == "time_metrics"]
    summary = _summarize(models)
    activity = _summarize(activity_rows)
    for field in ("active_seconds", "longest_continuous_seconds", "max_concurrent"):
        summary[field] = activity[field]

    lane_observations = [item for item in observations if item["lane"] == lane]
    collectors = _collectors(lane_observations, lane, now)
    expected_nodes = _expected_nodes(lane)
    coverage = node_coverage(
        expected_nodes,
        {item["node_id"]: item["status"] for item in collectors},
    )

    status = "degraded" if index["status"] in {"unavailable", "changing"} else "empty"
    if lane_rows:
        status = "degraded" if errors else "current"
    elif errors:
        status = "degraded"

    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "selected_lane": lane,
        "available_lanes": available_lanes,
        "filters": {
            key: filters.get(key, "")
            for key in ("node", "client", "provider", "model", "from", "to")
        },
        "facets": facets,
        "summary": summary,
        "series": _series(daily or models, "day"),
        "hourly": _series(hourly, "hour"),
        "breakdowns": {
            "models": _breakdown(models, "model"),
            "clients": _breakdown(models, "client"),
            "providers": _breakdown(models, "provider"),
            "nodes": _breakdown(models, "node_id"),
            "agents": _breakdown(agents, "agent"),
            "workspaces": _private_breakdown(workspaces, "workspace"),
            "sessions": _private_breakdown(sessions, "session"),
            "tasks": _private_breakdown(tasks, "task"),
        },
        "collectors": collectors,
        "coverage": coverage,
        "observation_count": len(lane_observations),
        "index": index,
        "sources": [
            {
                "source_path": observation["source_path"],
                "source_sha256": observation["source_sha256"],
                "source_bytes": observation["source_bytes"],
                "observed_at": observation["observed_at_text"],
                "payload_hash": observation["payload_hash"],
                "idempotency_key": observation["idempotency_key"],
                "measurement_lane": observation["lane"],
                "node_id": observation["node_id"],
                "principal_id": observation["principal_id"],
            }
            for observation in sorted(
                lane_observations,
                key=lambda item: (item["source_path"], item["observed_at_text"]),
            )
        ],
        "errors": errors,
    }
