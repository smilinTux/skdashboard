from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from skdashboard.dashboard_schedule import (
    AUTHORIZATION_TARGET,
    DATE_FIELDS,
    FIELD_PROVENANCE,
    SCHEMA_VERSION,
    ScheduleProjectionProvider,
)

NOW = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
TENANT = "public-synthetic"


class Context:
    def __init__(self, *, target=AUTHORIZATION_TARGET, capability="skdashboard.read", allow=True):
        self.binding = Mock(target=target, capability=capability)
        self.joined_decision = Mock(allow=allow)


class Verifier:
    def __init__(self, *states):
        self.states = iter(states or ("allow", "allow"))

    def check_before_owner_read(self, _context):
        return Mock(value=next(self.states))

    def check_after_owner_read(self, _context):
        return Mock(value=next(self.states))


class Source:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def read(self, context, request, home):
        self.calls.append((context, request, home))
        return deepcopy(self.snapshot)


class SlowSource(Source):
    def read(self, context, request, home):
        time.sleep(0.1)
        return super().read(context, request, home)


class SlowProjectionProvider(ScheduleProjectionProvider):
    def _project(self, raw, request, query):
        time.sleep(0.1)
        return super()._project(raw, request, query)


def _date(state="known", instant="2026-08-29T11:00:00Z", reason=None):
    value = {"state": state, "instant": instant}
    if state != "known":
        value.update(instant=None, reason=reason or f"canonical value is {state}")
    return value


def _item(item_id="release-1", semantic_type="release", *, truth_state="current"):
    dates = {field: _date() for field in DATE_FIELDS}
    dates["actual_finish"] = _date("not_applicable")
    dates["forecast_start"] = _date("partial")
    dates["forecast_target"] = _date("unavailable")
    return {
        "tenant_id": TENANT,
        "record_id": item_id,
        "display_title": "Synthetic release",
        "semantic_type": semantic_type,
        "owner_service_id": "skcoord",
        "service_id": "skdashboard",
        "lifecycle_status": "doing",
        "truth_state": truth_state,
        "visibility": {"state": "visible", "authorization": "authorized"},
        "dates": dates,
        "explicit_progress": None,
        "source_watermarks": [{"source": "synthetic.coordination", "value": "revision-7"}],
        "evidence_refs": [f"evidence://synthetic/{item_id}"],
    }


def _dependency(dependency_id, source_item_id, target_item_id):
    return {
        "tenant_id": TENANT,
        "dependency_id": dependency_id,
        "source_item_id": source_item_id,
        "target_item_id": target_item_id,
        "edge_type": "finish_to_start",
        "direction": "known",
        "lag_seconds": 0,
        "truth_state": "current",
        "visibility": {"state": "visible", "authorization": "authorized"},
        "blocker_state": "blocking",
        "evidence_refs": [f"evidence://synthetic/{dependency_id}"],
    }


def _snapshot():
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": TENANT,
        "snapshot_revision": "synthetic-revision-7",
        "observed_at": "2026-08-29T09:59:00Z",
        "projected_at": "2026-08-29T09:59:01Z",
        "authorization": {
            "state": "authorized",
            "target": AUTHORIZATION_TARGET,
            "tenant_id": TENANT,
            "role": "project_manager",
            "scope": "estate",
            "policy_decision_ref": "policy:synthetic-allow",
            "owner_policy_revision": "owner-policy-7",
        },
        "source_watermarks": [
            {"source": "synthetic.coordination", "value": "revision-7"},
            {"source": "synthetic.itil", "value": "revision-3"},
        ],
        "items": [
            _item("milestone-1", "milestone"),
            _item("release-1", "release"),
        ],
        "dependencies": [_dependency("dependency-1", "release-1", "milestone-1")],
        "overlays": [
            {
                "tenant_id": TENANT,
                "overlay_id": "change-window-1",
                "overlay_type": "itil_change_window",
                "owner_service_id": "skcapstone-itil",
                "start": _date(),
                "end": _date(instant="2026-08-29T12:00:00Z"),
                "truth_state": "current",
                "visibility": {"state": "visible", "authorization": "authorized"},
                "conflict_state": "clear",
                "evidence_refs": ["evidence://synthetic/change-window-1"],
            }
        ],
    }


def _query(lens="roadmap"):
    return {
        "role": "project-manager",
        "scope": "estate",
        "window": "latest",
        "baseline": "none",
        "service": "all",
        "lens": lens,
        "timezone": "UTC",
    }


def _read(snapshot=None, *, query=None, verifier=None):
    source = Source(snapshot or _snapshot())
    provider = ScheduleProjectionProvider(source, tenant_id=TENANT, clock=lambda: NOW)
    result = provider.read(
        Context(),
        query or _query(),
        Path("/synthetic"),
        currentness_verifier=verifier or Verifier(),
    )
    return result, source


def test_maps_pinned_canonical_sources_and_validates_contract() -> None:
    result, source = _read()
    schema_path = (
        Path(__file__).parents[1]
        / "docs/contracts/schedule/v1.0.0/control-plane-schedule-projection.v1.0.0.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        result
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["scope"] == {"role": "project_manager", "tenant_id": TENANT}
    assert result["field_provenance"] == dict(FIELD_PROVENANCE)
    assert result["items"][0]["item_type"] == "milestone"
    assert result["items"][1]["item_type"] == "release"
    assert result["dependencies"][0]["edge_type"] == "finish_to_start"
    assert result["overlays"][0]["overlay_type"] == "itil_change_window"
    assert result["projection_hash"].startswith("sha256:")
    request = source.calls[0][1]
    assert request.authorization_target == AUTHORIZATION_TARGET
    assert request.tenant_id == TENANT


def test_roadmap_gantt_and_flow_receive_identical_projection() -> None:
    encoded = []
    for lens in ("roadmap", "gantt", "flow"):
        result, _ = _read(query=_query(lens))
        encoded.append(json.dumps(result, sort_keys=True, separators=(",", ":")))
    assert len(set(encoded)) == 1


def test_truth_states_remain_distinct_and_nothing_is_invented_from_text() -> None:
    snapshot = _snapshot()
    item = snapshot["items"][0]
    item["display_title"] = "Finish tomorrow, 99% done, owned by Alice, depends on secret"
    item["dates"]["planned_start"] = _date("unknown")
    item["dates"]["planned_target"] = _date("stale")
    item["dates"]["actual_start"] = _date("policy_filtered")
    item["dates"]["actual_finish"] = _date("not_applicable")
    item["dates"]["forecast_start"] = _date("partial")
    item["dates"]["forecast_target"] = _date("unavailable")
    result, _ = _read(snapshot)
    projected = result["items"][0]
    assert [projected["dates"][field]["state"] for field in DATE_FIELDS] == [
        "known",
        "known",
        "unknown",
        "stale",
        "policy_filtered",
        "not_applicable",
        "partial",
        "unavailable",
    ]
    assert projected["progress"] is None
    assert projected["owner_service_id"] == "skcoord"
    assert len(result["dependencies"]) == 1
    assert result["critical_path"]["state"] == "unavailable"
    assert "missing_required_dates" in result["critical_path"]["reasons"]


@pytest.mark.parametrize(
    ("mutate", "context", "query"),
    [
        (lambda value: value.update(tenant_id="other-tenant"), Context(), _query()),
        (
            lambda value: value["authorization"].update(role="architect"),
            Context(),
            _query(),
        ),
        (
            lambda value: value["authorization"].update(scope="team"),
            Context(),
            _query(),
        ),
        (
            lambda value: value["authorization"].update(target="/api/v1/overview"),
            Context(),
            _query(),
        ),
        (
            lambda value: value["items"][0].update(tenant_id="other-tenant"),
            Context(),
            _query(),
        ),
        (lambda value: None, Context(target="/api/v1/overview"), _query()),
        (lambda value: None, Context(capability="other.read"), _query()),
        (lambda value: None, Context(allow=False), _query()),
    ],
)
def test_tenant_role_scope_owner_and_authorization_mismatch_fail_closed(
    mutate, context, query
) -> None:
    snapshot = _snapshot()
    mutate(snapshot)
    provider = ScheduleProjectionProvider(Source(snapshot), tenant_id=TENANT, clock=lambda: NOW)
    with pytest.raises(PermissionError, match="authorized schedule projection unavailable"):
        provider.read(context, query, Path("/synthetic"), currentness_verifier=Verifier())


def test_currentness_before_and_during_read_fail_closed() -> None:
    for states in (("deny",), ("allow", "deny")):
        provider = ScheduleProjectionProvider(
            Source(_snapshot()), tenant_id=TENANT, clock=lambda: NOW
        )
        with pytest.raises(PermissionError, match="authorized schedule projection unavailable"):
            provider.read(
                Context(),
                _query(),
                Path("/synthetic"),
                currentness_verifier=Verifier(*states),
            )


def test_owner_read_timeout_fails_closed() -> None:
    provider = ScheduleProjectionProvider(
        SlowSource(_snapshot()),
        tenant_id=TENANT,
        clock=lambda: NOW,
        owner_read_timeout_seconds=0.01,
        request_timeout_seconds=0.05,
    )
    with pytest.raises(PermissionError, match="authorized schedule projection unavailable"):
        provider.read(Context(), _query(), Path("/synthetic"), currentness_verifier=Verifier())


def test_repeated_hung_reads_have_bounded_threads() -> None:
    before = threading.active_count()
    gate = threading.Event()

    class HungSource(Source):
        def read(self, context, request, home):
            gate.wait()

    provider = ScheduleProjectionProvider(
        HungSource(_snapshot()),
        tenant_id=TENANT,
        clock=lambda: NOW,
        owner_read_timeout_seconds=0.01,
        request_timeout_seconds=0.02,
    )
    for _ in range(20):
        with pytest.raises(PermissionError):
            provider.read(Context(), _query(), Path("/synthetic"), currentness_verifier=Verifier())
    assert threading.active_count() <= before + 8
    gate.set()


def test_end_to_end_timeout_fails_closed() -> None:
    provider = SlowProjectionProvider(
        Source(_snapshot()),
        tenant_id=TENANT,
        clock=lambda: NOW,
        owner_read_timeout_seconds=0.01,
        request_timeout_seconds=0.02,
    )
    with pytest.raises(PermissionError, match="authorized schedule projection unavailable"):
        provider.read(Context(), _query(), Path("/synthetic"), currentness_verifier=Verifier())


def test_duplicate_items_and_self_reference_fail_in_production_provider() -> None:
    duplicate = _snapshot()
    duplicate["items"] = [_item("release-1"), _item("release-1")]
    self_reference = _snapshot()
    self_reference["dependencies"] = [_dependency("dependency-self", "release-1", "release-1")]
    for snapshot in (duplicate, self_reference):
        with pytest.raises(PermissionError, match="authorized schedule projection unavailable"):
            _read(snapshot)


def test_stale_and_unavailable_sources_fail_closed_without_record_detail() -> None:
    stale = _snapshot()
    stale["observed_at"] = "2026-08-29T09:00:00Z"
    providers = (
        ScheduleProjectionProvider(Source(stale), tenant_id=TENANT, clock=lambda: NOW),
        ScheduleProjectionProvider(Source(None), tenant_id=TENANT, clock=lambda: NOW),
    )
    for provider in providers:
        with pytest.raises(PermissionError) as error:
            provider.read(Context(), _query(), Path("/synthetic"), currentness_verifier=Verifier())
        assert "release-1" not in str(error.value)
        assert "other-tenant" not in str(error.value)


def test_policy_filtered_record_or_hidden_dependency_id_is_never_released() -> None:
    for mutation in ("record", "dependency"):
        snapshot = _snapshot()
        if mutation == "record":
            snapshot["items"][0]["visibility"] = {
                "state": "policy_filtered",
                "authorization": "denied",
                "policy_decision_ref": "policy:deny",
            }
        else:
            snapshot["dependencies"][0]["target_item_id"] = "protected-record-id"
        provider = ScheduleProjectionProvider(
            Source(snapshot), tenant_id=TENANT, clock=lambda: NOW
        )
        with pytest.raises(PermissionError) as error:
            provider.read(Context(), _query(), Path("/synthetic"), currentness_verifier=Verifier())
        assert "protected-record-id" not in str(error.value)
        assert "milestone-1" not in str(error.value)


def test_projection_is_bounded_and_linear_enough_for_large_synthetic_input() -> None:
    snapshot = _snapshot()
    snapshot["items"] = [_item(f"work-{index:05d}", "work_package") for index in range(2_000)]
    snapshot["dependencies"] = []
    snapshot["overlays"] = []
    result, _ = _read(snapshot)
    assert len(result["items"]) == 2_000
    assert result["individual_ranking_prohibited"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(snapshot_revision=""),
        lambda value: value["dependencies"][0].update(lag_seconds=True),
        lambda value: value.update(projected_at="2099-01-01T00:00:00Z"),
    ],
)
def test_malformed_revision_lag_and_future_projection_fail_closed(mutate) -> None:
    snapshot = _snapshot()
    mutate(snapshot)
    with pytest.raises(PermissionError) as error:
        _read(snapshot)
    assert str(error.value) == "authorized schedule projection unavailable"
    assert "release-1" not in str(error.value)


def test_long_dependency_chains_are_iterative_and_cycles_remain_explicit() -> None:
    snapshot = _snapshot()
    snapshot["items"] = [_item(f"work-{index:05d}", "work_package") for index in range(1_200)]
    snapshot["dependencies"] = [
        _dependency(f"dependency-{index:05d}", f"work-{index:05d}", f"work-{index + 1:05d}")
        for index in range(1_199)
    ]
    snapshot["overlays"] = []

    result, _ = _read(snapshot)
    assert len(result["items"]) == 1_200
    assert result["cycle_analysis"] == {
        "state": "acyclic",
        "cycle_item_ids": [],
        "evidence_refs": [],
    }

    snapshot["dependencies"].append(_dependency("dependency-cycle", "work-01199", "work-00000"))
    result, _ = _read(snapshot)
    assert result["cycle_analysis"]["state"] == "cycles_detected"
    assert len(result["cycle_analysis"]["cycle_item_ids"]) == 1_200
    assert result["critical_path"]["state"] == "unavailable"
    assert "dependency_cycle" in result["critical_path"]["reasons"]


def test_equivalent_dependency_permutations_share_projection_hash() -> None:
    snapshot = _snapshot()
    snapshot["items"].append(_item("work-1", "work_package"))
    snapshot["items"].sort(key=lambda value: value["record_id"])
    snapshot["dependencies"].append(_dependency("dependency-2", "work-1", "release-1"))
    reversed_snapshot = deepcopy(snapshot)
    reversed_snapshot["dependencies"].reverse()

    result, _ = _read(snapshot)
    reversed_result, _ = _read(reversed_snapshot)
    assert result == reversed_result
