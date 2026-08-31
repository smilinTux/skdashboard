"""Contract tests for the bounded chiap04 to chiap08 SKCounter transport."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).parents[1]
DOC_SCHEMA = (
    ROOT / "docs/contracts/skcounter/v1/skcounter-dashboard-aggregate-transport.v1.schema.json"
)
PACKAGE_SCHEMA = (
    ROOT
    / "src/skdashboard/contracts/skcounter/v1/skcounter-dashboard-aggregate-transport.v1.schema.json"
)
FIXTURE = ROOT / "tests/fixtures/skcounter_dashboard_aggregate_transport.v1.json"
CONTRACT = ROOT / "docs/contracts/SKCOUNTER-AGGREGATE-TRANSPORT-v1.md"
PROHIBITED_KEYS = {
    "api_key",
    "capability_token",
    "cookie",
    "credential",
    "prompt",
    "response",
    "session",
    "session_id",
    "source_path",
    "workspace_path",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_payload(document: dict) -> bytes:
    return json.dumps(
        {"records": document["records"], "provenance": document["provenance"]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_semantics(
    document: dict,
    *,
    now: datetime,
    previous: tuple[datetime, str] | None = None,
) -> str:
    payload = _canonical_payload(document)
    if document["rows"] != len(document["records"]):
        raise ValueError("row count mismatch")
    if document["payload_bytes"] != len(payload):
        raise ValueError("payload byte count mismatch")
    if document["payload_sha256"] != hashlib.sha256(payload).hexdigest():
        raise ValueError("payload hash mismatch")
    record_hashes = [
        hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for record in document["records"]
    ]
    if document["provenance"]["record_sha256s"] != record_hashes:
        raise ValueError("record provenance mismatch")
    generated = datetime.fromisoformat(document["generated_at"].replace("Z", "+00:00"))
    fresh_until = datetime.fromisoformat(document["fresh_until"].replace("Z", "+00:00"))
    newest = max(
        datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
        for record in document["records"]
    )
    if generated < newest or generated > now:
        raise ValueError("transport timestamps are inconsistent")
    if (fresh_until - newest).total_seconds() != 2700:
        raise ValueError("freshness TTL mismatch")
    if now > fresh_until:
        raise ValueError("bundle is stale")
    if previous:
        previous_generated, previous_hash = previous
        if generated < previous_generated:
            raise ValueError("older generation replay")
        if generated == previous_generated:
            if document["payload_sha256"] != previous_hash:
                raise ValueError("conflicting generation replay")
            return "unchanged"
    return "accepted"


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_schema_fixture_and_packaged_copy_are_exact() -> None:
    assert DOC_SCHEMA.read_bytes() == PACKAGE_SCHEMA.read_bytes()
    schema = _load(DOC_SCHEMA)
    Draft202012Validator.check_schema(schema)
    fixture = _load(FIXTURE)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixture)
    assert (
        _validate_semantics(fixture, now=datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc))
        == "accepted"
    )
    assert PROHIBITED_KEYS.isdisjoint(set(_keys(fixture)))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unexpected": "partial"}),
        lambda value: value.update({"rows": 10001}),
        lambda value: value["authority"].update({"host_fqdn": "chiap01.tail204f0c.ts.net"}),
        lambda value: value["target"].update({"capability": "skcounter.report.submit"}),
        lambda value: value["records"][0]["aggregate"].update({"prompt": "forbidden"}),
    ],
)
def test_schema_rejects_unbounded_unauthorized_or_raw_input(mutate) -> None:
    fixture = _load(FIXTURE)
    mutate(fixture)
    with pytest.raises(ValidationError):
        Draft202012Validator(_load(DOC_SCHEMA), format_checker=FormatChecker()).validate(fixture)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"rows": 2}, "row count mismatch"),
        ({"payload_bytes": 2}, "payload byte count mismatch"),
        ({"payload_sha256": "0" * 64}, "payload hash mismatch"),
        ({"generated_at": "2026-08-31T14:31:00Z"}, "transport timestamps"),
        ({"fresh_until": "2026-08-31T14:44:59Z"}, "freshness TTL"),
    ],
)
def test_semantics_reject_partial_replay_and_stale_boundaries(mutation, message) -> None:
    fixture = _load(FIXTURE)
    fixture.update(mutation)
    with pytest.raises(ValueError, match=message):
        _validate_semantics(fixture, now=datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc))

    stale = copy.deepcopy(_load(FIXTURE))
    with pytest.raises(ValueError, match="bundle is stale"):
        _validate_semantics(stale, now=datetime(2026, 8, 31, 14, 45, 1, tzinfo=timezone.utc))


def test_replay_is_idempotent_only_for_the_exact_generation() -> None:
    fixture = _load(FIXTURE)
    generated = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    previous = (generated, fixture["payload_sha256"])
    assert (
        _validate_semantics(
            fixture,
            now=datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc),
            previous=previous,
        )
        == "unchanged"
    )

    conflicting = copy.deepcopy(fixture)
    conflicting["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="payload hash mismatch|conflicting generation replay"):
        _validate_semantics(
            conflicting,
            now=datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc),
            previous=previous,
        )

    older = copy.deepcopy(fixture)
    older["generated_at"] = "2026-08-31T13:59:59Z"
    with pytest.raises(ValueError, match="transport timestamps|older generation replay"):
        _validate_semantics(
            older,
            now=datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc),
            previous=previous,
        )


def test_contract_pins_transport_outage_and_rollback() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for required in (
        "authenticated server-side pull",
        "Connect timeout: 2 seconds",
        "Response read timeout: 5 seconds",
        "Maximum response body: 1,048,576 bytes",
        "Maximum aggregate records: 10,000",
        "Currentness TTL: 2,700 seconds",
        "idempotent replay",
        "An outage never becomes zero usage",
        "Rollback disables this pull adapter",
        "Browsers never contact the collector",
    ):
        assert required in text
