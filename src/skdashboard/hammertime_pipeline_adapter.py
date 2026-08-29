"""Policy-gated HammerTime pipeline aggregate adapter.

This module accepts only an injected metadata-only aggregate mapping. It has no
filesystem, network, provider, credential, logging, metrics, or cache interface.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

SCHEMA_VERSION = "hammertime.pipeline.aggregate.v1"
POLICY_REF = "policy:hammertime-approved-aggregate-v1"
POLICY_TARGET = "skdashboard.now.hammertime.pipeline.aggregate"
AUDIENCE = "skdashboard.now"
PURPOSE = "pipeline_health_reporting"
EVIDENCE_TARGET = "urn:skdashboard:evidence:hammertime-pipeline-aggregate-v1"
FRESHNESS_SECONDS = 60
MAX_ROWS = 1
MAX_SOURCE_BYTES = 2_048
TIMEOUT_MS = 250
SOURCE_FIELDS = frozenset(
    {
        "schema_version",
        "policy",
        "source_revision",
        "observed_at",
        "source_truth_state",
        "aggregate",
        "evidence_target",
    }
)
COUNT_FIELDS = ("approved_releases", "pipeline_failures")
POLICY_FIELDS = frozenset({"decision", "policy_ref", "target", "audience", "purpose"})


@dataclass(frozen=True)
class HammerTimeAggregateFailure(Exception):
    code: str


def _failure(code: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_target": POLICY_TARGET,
        "audience": AUDIENCE,
        "purpose": PURPOSE,
        "evidence_target": EVIDENCE_TARGET,
        "truth_state": "unavailable",
        "aggregate": None,
        "error": {"code": code, "retryable": code in {"SOURCE_TIMEOUT", "SOURCE_UNAVAILABLE"}},
    }


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate(source: object, now: datetime) -> dict:
    try:
        encoded = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise HammerTimeAggregateFailure("SOURCE_MALFORMED") from exc
    if len(encoded) > MAX_SOURCE_BYTES or not isinstance(source, Mapping) or set(source) != SOURCE_FIELDS:
        raise HammerTimeAggregateFailure("SOURCE_MALFORMED")

    policy = source.get("policy")
    if not isinstance(policy, Mapping) or set(policy) != POLICY_FIELDS:
        raise HammerTimeAggregateFailure("POLICY_MISSING")
    if policy != {
        "decision": "allow",
        "policy_ref": POLICY_REF,
        "target": POLICY_TARGET,
        "audience": AUDIENCE,
        "purpose": PURPOSE,
    }:
        raise HammerTimeAggregateFailure("POLICY_DENIED")

    revision = source.get("source_revision")
    aggregate = source.get("aggregate")
    observed = _timestamp(source.get("observed_at"))
    if (
        source.get("schema_version") != SCHEMA_VERSION
        or source.get("source_truth_state") != "current"
        or source.get("evidence_target") != EVIDENCE_TARGET
        or not isinstance(revision, str)
        or len(revision) != 71
        or not revision.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in revision[7:])
        or observed is None
        or not isinstance(aggregate, Mapping)
        or set(aggregate) != set(COUNT_FIELDS)
        or any(
            not isinstance(aggregate[field], int)
            or isinstance(aggregate[field], bool)
            or aggregate[field] < 0
            or aggregate[field] > 1_000_000_000
            for field in COUNT_FIELDS
        )
    ):
        raise HammerTimeAggregateFailure("SOURCE_MALFORMED")

    age = (now - observed).total_seconds()
    if age < -300:
        raise HammerTimeAggregateFailure("SOURCE_MALFORMED")
    if age > FRESHNESS_SECONDS:
        raise HammerTimeAggregateFailure("SOURCE_STALE")
    if any(isinstance(value, float) and not math.isfinite(value) for value in aggregate.values()):
        raise HammerTimeAggregateFailure("SOURCE_MALFORMED")

    return {
        "schema_version": SCHEMA_VERSION,
        "policy_target": POLICY_TARGET,
        "audience": AUDIENCE,
        "purpose": PURPOSE,
        "evidence_target": EVIDENCE_TARGET,
        "source_revision": revision,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "freshness_seconds": FRESHNESS_SECONDS,
        "truth_state": "current",
        "aggregate": {field: aggregate[field] for field in COUNT_FIELDS},
        "error": None,
    }


def project_hammertime_pipeline(
    read_aggregate: Callable[[int], object] | None,
    *,
    now: datetime | None = None,
) -> dict:
    """Project one approved aggregate row, failing closed with fixed messages."""
    if read_aggregate is None:
        return _failure("SOURCE_UNAVAILABLE")
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        source = read_aggregate(TIMEOUT_MS)
        return _validate(source, instant)
    except TimeoutError:
        return _failure("SOURCE_TIMEOUT")
    except HammerTimeAggregateFailure as exc:
        return _failure(exc.code)
    except Exception:  # noqa: BLE001 - fixed failure output is the security boundary
        return _failure("SOURCE_UNAVAILABLE")
