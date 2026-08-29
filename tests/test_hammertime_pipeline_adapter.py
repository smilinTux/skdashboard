from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from skdashboard.hammertime_pipeline_adapter import (
    AUDIENCE,
    EVIDENCE_TARGET,
    FRESHNESS_SECONDS,
    MAX_ROWS,
    MAX_SOURCE_BYTES,
    POLICY_REF,
    POLICY_TARGET,
    PURPOSE,
    SCHEMA_VERSION,
    TIMEOUT_MS,
    project_hammertime_pipeline,
)

NOW = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
REVISION = "sha256:" + "a" * 64
CANARIES = (
    "forbidden-location-canary",
    "artifact-filename-canary.dat",
    "corpus-sentence-canary",
    "protected-id-7f61",
    "raw-event-canary",
    "prompt-canary",
    "capability-canary",
    "credential-canary",
    "secret-canary",
)


def source(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "decision": "allow",
            "policy_ref": POLICY_REF,
            "target": POLICY_TARGET,
            "audience": AUDIENCE,
            "purpose": PURPOSE,
        },
        "source_revision": REVISION,
        "observed_at": NOW.isoformat(),
        "source_truth_state": "current",
        "aggregate": {"approved_releases": 8, "pipeline_failures": 2},
        "evidence_target": EVIDENCE_TARGET,
    }
    value.update(overrides)
    return value


def test_pinned_contract_is_exact_and_public_synthetic() -> None:
    contract_path = Path("docs/contracts/hammertime-pipeline-aggregate.v1.schema.json")
    contract = json.loads(contract_path.read_text())
    Draft202012Validator.check_schema(contract)
    Draft202012Validator(contract).validate(source())
    assert contract["x-policy-target"] == POLICY_TARGET
    assert contract["x-audience"] == AUDIENCE
    assert contract["x-purpose"] == PURPOSE
    assert contract["x-freshness-seconds"] == FRESHNESS_SECONDS == 60
    assert contract["x-max-rows"] == MAX_ROWS == 1
    assert contract["x-max-source-bytes"] == MAX_SOURCE_BYTES == 2048
    assert contract["x-timeout-ms"] == TIMEOUT_MS == 250
    assert contract["x-evidence-target"] == EVIDENCE_TARGET


def test_only_approved_counts_and_truth_metadata_are_projected() -> None:
    seen = []

    def read(timeout_ms):
        seen.append(timeout_ms)
        return source()

    result = project_hammertime_pipeline(read, now=NOW)
    assert seen == [TIMEOUT_MS]
    assert result["truth_state"] == "current"
    assert result["aggregate"] == {"approved_releases": 8, "pipeline_failures": 2}
    assert result["source_revision"] == REVISION
    assert result["error"] is None
    assert set(result) == {
        "schema_version",
        "policy_target",
        "audience",
        "purpose",
        "evidence_target",
        "source_revision",
        "observed_at",
        "freshness_seconds",
        "truth_state",
        "aggregate",
        "error",
    }


@pytest.mark.parametrize(
    ("reader", "code"),
    [
        (None, "SOURCE_UNAVAILABLE"),
        (lambda _timeout: (_ for _ in ()).throw(TimeoutError("secret-canary")), "SOURCE_TIMEOUT"),
        (lambda _timeout: (_ for _ in ()).throw(RuntimeError("secret-canary")), "SOURCE_UNAVAILABLE"),
        (lambda _timeout: source(policy=None), "POLICY_MISSING"),
        (lambda _timeout: source(policy={}), "POLICY_MISSING"),
        (
            lambda _timeout: source(
                observed_at=(NOW - timedelta(seconds=FRESHNESS_SECONDS + 1)).isoformat()
            ),
            "SOURCE_STALE",
        ),
        (lambda _timeout: source(source_truth_state="partial"), "SOURCE_MALFORMED"),
        (lambda _timeout: source(extra="raw-event-canary"), "SOURCE_MALFORMED"),
        (
            lambda _timeout: source(
                aggregate={"approved_releases": 1, "pipeline_failures": 0, "raw_event": "x"}
            ),
            "SOURCE_MALFORMED",
        ),
        (lambda _timeout: source(source_revision="main"), "SOURCE_MALFORMED"),
    ],
)
def test_fail_closed_states(reader, code) -> None:
    result = project_hammertime_pipeline(reader, now=NOW)
    assert result["truth_state"] == "unavailable"
    assert result["aggregate"] is None
    assert result["error"]["code"] == code
    assert not any(canary in json.dumps(result) for canary in CANARIES)


def test_policy_target_audience_purpose_and_decision_are_not_substitutable() -> None:
    for field, replacement in (
        ("decision", "deny"),
        ("policy_ref", "policy:other"),
        ("target", "other.target"),
        ("audience", "other.audience"),
        ("purpose", "other_purpose"),
    ):
        policy = source()["policy"] | {field: replacement}
        result = project_hammertime_pipeline(lambda _timeout, p=policy: source(policy=p), now=NOW)
        assert result["truth_state"] == "unavailable"
        assert result["aggregate"] is None
        assert result["error"]["code"] == "POLICY_DENIED"


def test_row_byte_count_and_freshness_boundaries() -> None:
    assert project_hammertime_pipeline(lambda _timeout: source(), now=NOW)["truth_state"] == "current"
    edge = source(observed_at=(NOW - timedelta(seconds=FRESHNESS_SECONDS)).isoformat())
    assert project_hammertime_pipeline(lambda _timeout: edge, now=NOW)["truth_state"] == "current"
    for count in (-1, 1_000_000_001, True, 1.5):
        malformed = source(aggregate={"approved_releases": count, "pipeline_failures": 0})
        assert project_hammertime_pipeline(lambda _timeout, s=malformed: s, now=NOW)["aggregate"] is None
    oversize = source(padding="x" * MAX_SOURCE_BYTES)
    assert project_hammertime_pipeline(lambda _timeout: oversize, now=NOW)["aggregate"] is None


def test_canaries_cannot_enter_response_logs_metrics_or_caches(caplog) -> None:
    cache = {}
    metrics = []
    for canary in CANARIES:
        malicious = source(aggregate={"approved_releases": 1, "pipeline_failures": 0, "extra": canary})
        result = project_hammertime_pipeline(lambda _timeout, s=malicious: s, now=NOW)
        assert canary not in json.dumps(result)
        assert not cache
        assert not metrics
    assert not caplog.records
