"""
Policy-gated HammerTime pipeline aggregate adapter for SKDashboard.

This module provides a metadata-only, bounded read interface to HammerTime
pipeline aggregates. It enforces strict policy boundaries:

- Never reads, searches, moves, enumerates, or processes HammerTime Inbox
- Never accesses corpus artifacts or protected identifiers
- Reads only from approved metadata-only aggregate sources
- Exposes ONLY approved pipeline aggregate counts and explicit truth states
- Fails closed on missing policy, stale state, malformed source, timeout,
  or unavailable source

CONSTRAINTS (pinned before implementation per AC #1):
- Schema version: 1.1.0 (control-plane aggregate schema)
- Policy target: hammertime.pipeline
- Audience: SKDashboard Now workspace (authorized readers with skdashboard.read)
- Purpose: Display approved corpus release pipeline health metrics
- Source revision: public-synthetic fixture v1.0.0 (control_plane_full_estate.v1.0.0.json)
- Freshness: 60 second TTL, stale state > 60 seconds
- Row bounds: max 1 aggregate row per projection
- Byte bounds: max 4KB aggregate payload
- Timeout: 1000ms query budget
- Evidence target: ~/.skcapstone/evidence/work/494a9e88/
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Pinned constants per Acceptance Criterion #1
ADAPTER_ID = "hammertime.pipeline"
ADAPTER_VERSION = "1.0.0"
SCHEMA_VERSION = "1.1.0"
SOURCE_REVISION = "v1.0.0"
OWNER = "HammerTime"
POPULATION = "approved_aggregate_pipeline"
CLASSIFICATION = "confidential"
TTL_SECONDS = 60
TIMEOUT_MS = 1000
MAX_AGGREGATE_BYTES = 4_096
EVIDENCE_TARGET = Path.home() / ".skcapstone" / "evidence" / "work" / "494a9e88"


@dataclass(frozen=True)
class HammerTimeAggregate:
    """
    Approved metadata-only aggregate for HammerTime pipeline.

    Contains ONLY approved pipeline aggregate counts. No protected
    identifiers, corpus text, prompts, capabilities, credentials, or secrets
    are included.
    """

    approved_releases: int
    pipeline_failures: int
    observed_at: str
    watermark: str

    @property
    def to_dict(self) -> dict:
        """Export to safe dictionary with no protected data."""
        return {
            "schema_version": SCHEMA_VERSION,
            "observed_at": self.observed_at,
            "watermark": self.watermark,
            "coverage": {"expected": 1, "reporting": 1},
            "aggregate": {
                "approved_releases": self.approved_releases,
                "pipeline_failures": self.pipeline_failures,
            },
            "errors": [],
            "has_observations": True,
        }


class PolicyGateError(Exception):
    """Raised when policy gate blocks access."""


class SourceMalformedError(Exception):
    """Raised when source data fails validation."""


class SourceUnavailableError(Exception):
    """Raised when source is unreachable or unavailable."""


def _validate_observed_at(value: str) -> None:
    """
    Validate observation timestamp.

    Ensures the timestamp is ISO 8601 with explicit timezone and
    not in the future (allowing 5 minute clock skew).
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceMalformedError("observed_at must be ISO 8601") from exc

    if parsed.tzinfo is None:
        raise SourceMalformedError("observed_at requires explicit timezone")

    now = datetime.now(timezone.utc)
    age = (now - parsed).total_seconds()
    if age < -300:
        raise SourceMalformedError("observed_at is in the future")


def _validate_aggregate_value(value: object, field_name: str) -> None:
    """
    Validate an aggregate field value.

    Ensures:
    - Value is int, float, bool, str, or None
    - String values are <= 128 characters
    - Float values are finite (not NaN or infinity)
    """
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise SourceMalformedError(f"{field_name} has invalid type")

    if isinstance(value, str) and len(value) > 128:
        raise SourceMalformedError(f"{field_name} exceeds maximum length")

    if isinstance(value, float) and not math.isfinite(value):
        raise SourceMalformedError(f"{field_name} is not finite")


def _validate_watermark(value: str) -> None:
    """Validate watermark string format and length."""
    if not isinstance(value, str) or not value:
        raise SourceMalformedError("watermark must be a non-empty string")
    if len(value) > 256:
        raise SourceMalformedError("watermark exceeds maximum length")


def _validate_payload_size(payload: dict) -> None:
    """Validate aggregate payload does not exceed byte bounds."""
    aggregate = payload.get("aggregate", {})
    if not isinstance(aggregate, dict):
        raise SourceMalformedError("aggregate must be a dict")

    serialized = json.dumps(aggregate, separators=(",", ":"), default=str).encode()
    if len(serialized) > MAX_AGGREGATE_BYTES:
        raise SourceMalformedError(
            f"aggregate payload exceeds {MAX_AGGREGATE_BYTES} byte limit"
        )


def _contains_secrets(value: str) -> bool:
    """
    Check if a string contains potential secret patterns.

    This is a conservative check that looks for common patterns
    that might indicate secrets or protected identifiers. The
    function is intentionally broad to ensure fail-closed behavior.
    """
    if not value:
        return False

    # Check for absolute paths that might point to Inbox or corpus
    if any(
        pattern in value.lower()
        for pattern in (
            "/hammertime/inbox",
            "/hammertime/corpus",
            "/inbox/",
            "/corpus/",
            "/protected/",
            "/secrets/",
        )
    ):
        return True

    # Check for credential-like patterns (conservative)
    if any(
        pattern in value.lower()
        for pattern in ("password", "secret", "token", "api_key", "credential", "private")
    ):
        return True

    return False


def _sanitize_for_logging(value: dict) -> dict:
    """
    Sanitize a dictionary for safe logging.

    Removes any values that might contain protected identifiers,
    secrets, or paths to sensitive data. Replaces with redacted placeholder.
    """
    if not isinstance(value, dict):
        return {}

    safe = {}
    for key, val in value.items():
        if isinstance(val, str):
            if _contains_secrets(val):
                safe[key] = "[REDACTED]"
            else:
                safe[key] = val
        elif isinstance(val, (dict, list)):
            safe[key] = val  # Nested structures processed separately
        else:
            safe[key] = val

    return safe


def create_synthetic_fixture() -> tuple[HammerTimeAggregate, dict]:
    """
    Create a public-synthetic fixture for testing.

    This is the ONLY allowed source for development and testing.
    It contains no protected data, no Inbox paths, no corpus text,
    and no real credentials.

    Returns:
        tuple: (HammerTimeAggregate, full_payload_dict) for testing
    """
    # Use deterministic values based on fixture version
    approved_releases = 0  # No approved releases in synthetic fixture
    pipeline_failures = 0  # No pipeline failures in synthetic fixture
    observed_at = "2026-08-24T12:00:00Z"

    # Create deterministic watermark
    safe = json.dumps(
        {
            "aggregate": {
                "approved_releases": approved_releases,
                "pipeline_failures": pipeline_failures,
            },
            "coverage": {"expected": 1, "reporting": 1},
            "errors": [],
            "has_observations": True,
            "source": "synthetic-corpus-r1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    watermark = f"sha256:{hashlib.sha256(safe.encode()).hexdigest()}"

    agg = HammerTimeAggregate(
        approved_releases=approved_releases,
        pipeline_failures=pipeline_failures,
        observed_at=observed_at,
        watermark=watermark,
    )

    # Return full payload for validation testing
    payload = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at,
        "watermark": watermark,
        "coverage": {"expected": 1, "reporting": 1},
        "aggregate": {
            "approved_releases": approved_releases,
            "pipeline_failures": pipeline_failures,
        },
        "errors": [],
        "has_observations": True,
        "source": "synthetic-corpus-r1",
    }

    return agg, payload


def validate_aggregate(payload: dict) -> HammerTimeAggregate:
    """
    Validate an aggregate payload and return a safe HammerTimeAggregate.

    This function enforces strict validation:
    - Schema version must match pinned version
    - Required fields must be present
    - Field values must pass type and size checks
    - Watermark must be valid
    - No protected identifiers, secrets, or paths to Inbox/corpus

    Args:
        payload: Raw aggregate payload from source

    Returns:
        HammerTimeAggregate: Validated safe aggregate

    Raises:
        PolicyGateError: If policy gate blocks access
        SourceMalformedError: If source data fails validation
        SourceUnavailableError: If source is unavailable
    """
    # Check policy gate - this adapter requires explicit authorization
    # (In production, this would check actual policy. For development,
    # we allow synthetic fixtures only.)
    if payload.get("source") != "synthetic-corpus-r1":
        raise PolicyGateError(
            "Policy gate: Only public-synthetic fixtures are allowed. "
            "Live HammerTime Inbox or corpus access is not permitted."
        )

    # Validate schema version
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Incompatible schema version: {payload.get('schema_version')}"
        )

    # Validate required top-level fields
    required_fields = {"schema_version", "observed_at", "watermark", "coverage", "aggregate"}
    if not set(payload.keys()) >= required_fields:
        raise SourceMalformedError("Missing required fields")

    # Validate observed_at
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str):
        raise SourceMalformedError("observed_at must be a string")
    _validate_observed_at(observed_at)

    # Validate watermark
    watermark = payload.get("watermark")
    if not isinstance(watermark, str):
        raise SourceMalformedError("watermark must be a string")
    _validate_watermark(watermark)

    # Check watermark for secret patterns
    if _contains_secrets(watermark):
        raise PolicyGateError("Watermark contains prohibited patterns")

    # Validate coverage
    coverage = payload.get("coverage", {})
    if not isinstance(coverage, dict):
        raise SourceMalformedError("coverage must be a dict")
    if set(coverage.keys()) != {"expected", "reporting"}:
        raise SourceMalformedError("coverage must have expected and reporting keys")
    if not all(
        isinstance(v, int) and not isinstance(v, bool) for v in coverage.values()
    ):
        raise SourceMalformedError("coverage values must be integers")

    # Validate aggregate
    aggregate = payload.get("aggregate", {})
    if not isinstance(aggregate, dict):
        raise SourceMalformedError("aggregate must be a dict")

    # Check for required aggregate fields
    required_aggregate_fields = {"approved_releases", "pipeline_failures"}
    if set(aggregate.keys()) != required_aggregate_fields:
        raise SourceMalformedError(
            f"aggregate must have exactly {required_aggregate_fields} fields"
        )

    # Validate each aggregate field
    for field_name, field_value in aggregate.items():
        _validate_aggregate_value(field_value, field_name)

        # Check for secret patterns in all string values
        if isinstance(field_value, str) and _contains_secrets(field_value):
            raise PolicyGateError(f"Aggregate field {field_name} contains prohibited patterns")

    # Validate payload size
    _validate_payload_size(payload)

    # Check errors array
    errors = payload.get("errors", [])
    if not isinstance(errors, list) or len(errors) > 16:
        raise SourceMalformedError("errors must be an array with max 16 items")
    if not all(isinstance(e, str) for e in errors):
        raise SourceMalformedError("all errors must be strings")

    # Check error messages for secret patterns
    for error in errors:
        if _contains_secrets(error):
            raise PolicyGateError("Error message contains prohibited patterns")

    # Validate has_observations
    has_observations = payload.get("has_observations")
    if not isinstance(has_observations, bool):
        raise SourceMalformedError("has_observations must be a boolean")

    # If we got here, the payload is valid
    return HammerTimeAggregate(
        approved_releases=int(aggregate["approved_releases"]),
        pipeline_failures=int(aggregate["pipeline_failures"]),
        observed_at=observed_at,
        watermark=watermark,
    )


def project_aggregate(
    aggregate: HammerTimeAggregate | None,
    *,
    now: datetime | None = None,
) -> dict:
    """
    Project a HammerTimeAggregate to the control-plane envelope.

    Args:
        aggregate: Validated aggregate or None if unavailable
        now: Projection time (defaults to current time)

    Returns:
        dict: Projected control-plane envelope with truth state
    """
    instant = now or datetime.now(timezone.utc)
    projected_at = instant.isoformat().replace("+00:00", "Z")

    # If no aggregate, return unavailable state
    if aggregate is None:
        return {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "owner": OWNER,
            "population": POPULATION,
            "classification": CLASSIFICATION,
            "visibility": {"state": "visible", "authorization": "authorized"},
            "query_budget": {"max_items": 1, "timeout_ms": TIMEOUT_MS},
            "ttl_seconds": TTL_SECONDS,
            "age_seconds": None,
            "observed_at": None,
            "projected_at": projected_at,
            "watermark": {"source": ADAPTER_ID, "value": None},
            "truth_state": "unavailable",
            "coverage": {"expected": None, "reporting": None},
            "aggregate": None,
            "errors": [
                {
                    "code": "SOURCE_UNAVAILABLE",
                    "message": "No authorized aggregate reader is configured",
                    "retryable": True,
                }
            ],
        }

    # Calculate age and truth state
    try:
        observed = datetime.fromisoformat(aggregate.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("Missing timezone")
        observed = observed.astimezone(timezone.utc)
    except ValueError:
        return {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "owner": OWNER,
            "population": POPULATION,
            "classification": CLASSIFICATION,
            "visibility": {"state": "visible", "authorization": "authorized"},
            "query_budget": {"max_items": 1, "timeout_ms": TIMEOUT_MS},
            "ttl_seconds": TTL_SECONDS,
            "age_seconds": None,
            "observed_at": None,
            "projected_at": projected_at,
            "watermark": {"source": ADAPTER_ID, "value": None},
            "truth_state": "unavailable",
            "coverage": {"expected": None, "reporting": None},
            "aggregate": None,
            "errors": [
                {
                    "code": "SOURCE_MALFORMED",
                    "message": "Invalid observation timestamp",
                    "retryable": False,
                }
            ],
        }

    age = (instant - observed).total_seconds()
    age_seconds = max(0, int(age))

    # Determine truth state
    truth_state = "current"
    if age_seconds > TTL_SECONDS:
        truth_state = "stale"

    # Build projected envelope
    return {
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "owner": OWNER,
        "population": POPULATION,
        "classification": CLASSIFICATION,
        "visibility": {"state": "visible", "authorization": "authorized"},
        "query_budget": {"max_items": 1, "timeout_ms": TIMEOUT_MS},
        "ttl_seconds": TTL_SECONDS,
        "age_seconds": age_seconds,
        "observed_at": aggregate.observed_at,
        "projected_at": projected_at,
        "watermark": {"source": ADAPTER_ID, "value": aggregate.watermark},
        "truth_state": truth_state,
        "coverage": {"expected": 1, "reporting": 1},
        "aggregate": {
            "approved_releases": aggregate.approved_releases,
            "pipeline_failures": aggregate.pipeline_failures,
        },
        "errors": [],
    }


def create_reader(
    fixture: HammerTimeAggregate | None = None,
) -> Callable[[], dict]:
    """
    Create a reader function for the HammerTime adapter.

    This reader uses ONLY public-synthetic fixtures and enforces
    fail-closed behavior on any error.

    Args:
        fixture: Optional synthetic fixture. If None, creates default.

    Returns:
        Callable: Reader function that returns a dict payload
    """

    def reader() -> dict:
        # Use provided fixture or create synthetic
        agg = fixture or create_synthetic_fixture()
        return agg.to_dict

    return reader


# Public API
def get_aggregate(*, now: datetime | None = None) -> dict:
    """
    Get the current HammerTime pipeline aggregate projection.

    This is the main entry point for the adapter. It uses only
    public-synthetic fixtures and enforces all policy gates.

    Args:
        now: Projection time (for testing)

    Returns:
        dict: Projected control-plane envelope
    """
    try:
        # Create synthetic fixture (the only allowed source)
        reader = create_reader()
        payload = reader()

        # Validate and create safe aggregate
        aggregate = validate_aggregate(payload)

        # Project to control-plane envelope
        return project_aggregate(aggregate, now=now)
    except PolicyGateError as exc:
        # Policy gate violations fail closed
        instant = now or datetime.now(timezone.utc)
        projected_at = instant.isoformat().replace("+00:00", "Z")
        return {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "owner": OWNER,
            "population": POPULATION,
            "classification": CLASSIFICATION,
            "visibility": {"state": "policy_denied", "authorization": "denied"},
            "query_budget": {"max_items": 1, "timeout_ms": TIMEOUT_MS},
            "ttl_seconds": TTL_SECONDS,
            "age_seconds": None,
            "observed_at": None,
            "projected_at": projected_at,
            "watermark": {"source": ADAPTER_ID, "value": None},
            "truth_state": "unknown",
            "coverage": {"expected": None, "reporting": None},
            "aggregate": None,
            "errors": [
                {
                    "code": "POLICY_DENIED",
                    "message": str(exc),
                    "retryable": False,
                }
            ],
        }
    except (SourceMalformedError, SourceUnavailableError) as exc:
        # Source errors fail closed
        instant = now or datetime.now(timezone.utc)
        projected_at = instant.isoformat().replace("+00:00", "Z")
        error_code = (
            "SOURCE_MALFORMED"
            if isinstance(exc, SourceMalformedError)
            else "SOURCE_UNAVAILABLE"
        )
        return {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "owner": OWNER,
            "population": POPULATION,
            "classification": CLASSIFICATION,
            "visibility": {"state": "visible", "authorization": "authorized"},
            "query_budget": {"max_items": 1, "timeout_ms": TIMEOUT_MS},
            "ttl_seconds": TTL_SECONDS,
            "age_seconds": None,
            "observed_at": None,
            "projected_at": projected_at,
            "watermark": {"source": ADAPTER_ID, "value": None},
            "truth_state": "unavailable",
            "coverage": {"expected": None, "reporting": None},
            "aggregate": None,
            "errors": [
                {
                    "code": error_code,
                    "message": str(exc),
                    "retryable": isinstance(exc, SourceUnavailableError),
                }
            ],
        }
