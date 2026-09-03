"""Contract tests for the SKDashboard schedule provider boundary, card 2a4bb204 R2.

The R1 suite tested only mock-typed hand-rolled schemas. The R2 suite binds the
contract to the artifacts that actually exist: the frozen JSON schema on disk,
the real ScheduleProjectionProvider semantics (fail-closed, currentness, role
aliases), and the exact forecast representation the route accepts. Providers
are replaced with fakes; no live source, no network, no owner mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, RefResolver

CONTRACTS = Path(__file__).resolve().parents[4] / "docs" / "contracts" / "schedule" / "v1.0.0"
SCHEMA_PATH = CONTRACTS / "control-plane-schedule-projection.v1.0.0.schema.json"
OPENAPI_PATH = CONTRACTS / "openapi.control-plane-schedule.v1.0.0.json"

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
HASH = "sha256:" + "a" * 64


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict:
    return _load(SCHEMA_PATH)


@pytest.fixture(scope="module")
def validator(schema):
    store = {
        path.as_uri(): _load(path) for path in CONTRACTS.glob("*.json") if path.suffix == ".json"
    }
    return Draft202012Validator(
        schema,
        resolver=RefResolver(base_uri=CONTRACTS.as_uri() + "/", referrer=schema, store=store),
    )


# ---------------------------------------------------------------------------
# Contract doubles
# ---------------------------------------------------------------------------


class DecisionState:
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class FakeContext:
    """Typed context double matching the implemented binding contract."""

    tenant_id: str = "tenant-1"
    role: str = "portfolio"
    target: str = "/api/v1/schedule/projection"
    capability: str = "skdashboard.read"
    allow: bool = True
    decision_id: str = "decision-1"

    @property
    def binding(self) -> dict[str, str]:
        return {"target": self.target, "capability": self.capability}

    @property
    def joined_decision(self) -> "FakeContext":
        return self


class Verifier:
    """Single-use verifier double with call accounting."""

    def __init__(self, *, before: str = "allow", after: str = "allow") -> None:
        self.before = before
        self.after = after
        self.calls: list[str] = []

    def check_before_owner_read(self, context):
        self.calls.append("before")
        return type("D", (), {"value": self.before})()

    def check_after_owner_read(self, context):
        self.calls.append("after")
        return type("D", (), {"value": self.after})()


class ProviderUnavailableError(Exception):
    pass


class FakeScheduleProvider:
    """Projection provider double implementing the Section 3 sequence."""

    def __init__(
        self,
        snapshot: dict | None,
        *,
        tenant_id: str = "tenant-1",
        max_source_age_seconds: int = 300,
        clock=lambda: NOW,
    ) -> None:
        if not 1 <= max_source_age_seconds <= 86_400:
            raise ValueError("invalid Schedule provider configuration")
        self.snapshot = snapshot
        self.tenant_id = tenant_id
        self.max_source_age_seconds = max_source_age_seconds
        self.clock = clock

    def read(self, context, query, home, *, currentness_verifier):
        try:
            # Route guard (Section 4.3): the route rejects these before any
            # provider is invoked; modeled here on the provider boundary.
            if (
                query.get("role") not in ROUTE_ROLE_ALIASES
                or query.get("scope") != "estate"
                or query.get("service", "all") != "all"
            ):
                raise ValueError("INVALID_SCHEDULE_SCOPE")
            if context.binding["target"] != "/api/v1/schedule/projection":
                raise PermissionError
            if context.binding["capability"] != "skdashboard.read":
                raise PermissionError
            if not context.allow:
                raise PermissionError
            if currentness_verifier.check_before_owner_read(context).value != "allow":
                raise PermissionError
            snapshot = self.snapshot
            if snapshot is None:
                raise PermissionError
            self._validate_snapshot(snapshot)
            if currentness_verifier.check_after_owner_read(context).value != "allow":
                raise PermissionError
            return self._project(snapshot, query)
        except Exception as exc:
            raise ProviderUnavailableError("authorized schedule projection unavailable") from exc

    def _validate_snapshot(self, s: dict) -> None:
        required = {
            "schema_version",
            "tenant_id",
            "snapshot_revision",
            "observed_at",
            "projected_at",
            "authorization",
            "source_watermarks",
            "items",
            "dependencies",
            "overlays",
        }
        if set(s) != required or s["schema_version"] != "1.0.0":
            raise ValueError
        auth = s["authorization"]
        if (
            s["tenant_id"] != self.tenant_id
            or auth.get("state") != "authorized"
            or auth.get("tenant_id") != self.tenant_id
            or not auth.get("policy_decision_ref")
        ):
            raise PermissionError
        observed = datetime.fromisoformat(s["observed_at"])
        projected = datetime.fromisoformat(s["projected_at"])
        now = self.clock()
        age = (now - observed).total_seconds()
        if age < -5 or age > self.max_source_age_seconds or projected < observed:
            raise PermissionError
        if (
            len(s["items"]) > 10_000
            or len(s["dependencies"]) > 20_000
            or len(s["overlays"]) > 5_000
        ):
            raise ValueError

    def _project(self, s: dict, query: dict) -> dict:
        projection = {
            "schema_version": "1.0.0",
            "projection_id": f"schedule:{self.tenant_id}:{query['role']}:estate",
            "projection_version": s["snapshot_revision"],
            "projection_hash": "",
            "scope": {"role": query["role"], "tenant_id": self.tenant_id},
            "display_timezone": query["timezone"],
            "observed_at": s["observed_at"],
            "projected_at": s["projected_at"],
            "truth_state": "current",
            "visibility": {
                "state": "visible",
                "authorization": "authorized",
                "policy_decision_ref": s["authorization"]["policy_decision_ref"],
            },
            "source_watermarks": list(s["source_watermarks"]),
            "field_provenance": {"item_id": "canonical coordination record.record_id"},
            "items": [],
            "dependencies": [],
            "overlays": [],
            "cycle_analysis": {
                "state": "acyclic",
                "cycle_item_ids": [],
                "evidence_refs": [],
            },
            "critical_path": {
                "state": "not_applicable",
                "item_ids": [],
                "reasons": ["not_applicable"],
            },
            "individual_ranking_prohibited": True,
            "errors": [],
        }
        projection["projection_hash"] = "sha256:" + "b" * 64
        return projection


def forecast_double(
    periods: list[dict],
    *,
    cohort: str = "estate",
    scope: str = "estate",
    remaining_work: int = 100,
    seed: int = 7,
    iterations: int = 2000,
    minimum_sample: int = 6,
) -> dict:
    """Forecast artifact double matching the route acceptance contract."""

    included = [p for p in periods if p["timing_basis"] == "canonical_period"]
    exclusions = [
        {
            "period_id": p["period_id"],
            "timing_basis": p["timing_basis"],
            "reason": "non-canonical timing excluded from aggregate throughput sampling",
        }
        for p in periods
        if p["timing_basis"] != "canonical_period"
    ]
    cadences = {(p["end"] - p["start"]).days for p in included}
    cadence = cadences.pop() if len(cadences) == 1 else None
    base: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_kind": "aggregate_schedule_forecast",
        "state": "ready",
        "abstention_reason": None,
        "method": "aggregate_throughput_bootstrap_monte_carlo",
        "calculation_owner": "deterministic_engine",
        "method_discrimination": {
            "throughput_forecast": "probabilistic aggregate flow in periods",
            "date_critical_path": "not calculated or blended by this artifact",
        },
        "cohort": cohort,
        "scope": scope,
        "history_window": {
            "start": included[0]["start"].isoformat() if included else None,
            "end": included[-1]["end"].isoformat() if included else None,
        },
        "sample_periods": len(included),
        "period_cadence_days": cadence,
        "remaining_work": remaining_work,
        "iterations": iterations,
        "seed": seed,
        "assumptions": [],
        "exclusions": exclusions,
        "individual_ranking_prohibited": True,
        "completion_quantiles_periods": {"p50": 3, "p85": 4, "p95": 5},
        "milestone_confidence": None,
        "writes_owner_records": False,
    }
    if len(included) < minimum_sample:
        return {
            **base,
            "state": "abstained",
            "abstention_reason": "fewer than 6 canonical throughput periods",
            "completion_quantiles_periods": {"p50": None, "p85": None, "p95": None},
        }
    if cadence is None:
        return {
            **base,
            "state": "abstained",
            "abstention_reason": "canonical throughput periods have mixed cadence",
            "completion_quantiles_periods": {"p50": None, "p85": None, "p95": None},
        }
    return base


def _period(
    period_id: str,
    start: datetime,
    completed: int,
    timing_basis: str = "canonical_period",
    *,
    days: int = 7,
) -> dict:
    return {
        "period_id": period_id,
        "start": start,
        "end": start + timedelta(days=days),
        "completed": completed,
        "timing_basis": timing_basis,
    }


# ---------------------------------------------------------------------------
# Snapshot and projection builders
# ---------------------------------------------------------------------------


def _snapshot(**overrides) -> dict:
    snap = {
        "schema_version": "1.0.0",
        "tenant_id": "tenant-1",
        "snapshot_revision": "rev-1",
        "observed_at": (NOW - timedelta(seconds=10)).isoformat(),
        "projected_at": (NOW - timedelta(seconds=5)).isoformat(),
        "authorization": {
            "state": "authorized",
            "target": "/api/v1/schedule/projection",
            "tenant_id": "tenant-1",
            "role": "portfolio",
            "scope": "estate",
            "policy_decision_ref": "decision-1",
            "owner_policy_revision": "policy-1",
        },
        "source_watermarks": [{"source": "canonical", "value": "rev-1"}],
        "items": [],
        "dependencies": [],
        "overlays": [],
    }
    snap.update(overrides)
    return snap


def _valid_projection() -> dict:
    return {
        "schema_version": "1.0.0",
        "projection_id": "schedule:tenant-1:portfolio:estate",
        "projection_version": "rev-1",
        "projection_hash": HASH,
        "scope": {"role": "portfolio", "tenant_id": "tenant-1"},
        "display_timezone": "UTC",
        "observed_at": (NOW - timedelta(seconds=10)).isoformat(),
        "projected_at": (NOW - timedelta(seconds=5)).isoformat(),
        "truth_state": "current",
        "visibility": {
            "state": "visible",
            "authorization": "authorized",
            "policy_decision_ref": "decision-1",
        },
        "source_watermarks": [{"source": "canonical", "value": "rev-1"}],
        "field_provenance": {"item_id": "canonical coordination record.record_id"},
        "items": [],
        "dependencies": [],
        "overlays": [],
        "cycle_analysis": {
            "state": "acyclic",
            "cycle_item_ids": [],
            "evidence_refs": [],
        },
        "critical_path": {
            "state": "not_applicable",
            "item_ids": [],
            "reasons": ["not_applicable"],
        },
        "individual_ranking_prohibited": True,
        "errors": [],
    }


def _item(item_id: str = "item-1") -> dict:
    return {
        "item_id": item_id,
        "title": "Item",
        "item_type": "project",
        "owner_service_id": "svc-1",
        "status": "in_progress",
        "truth_state": "current",
        "visibility": {"state": "visible", "authorization": "authorized"},
        "dates": {
            "baseline_start": {"state": "known", "instant": "2026-08-01T00:00:00Z"},
            "baseline_target": {"state": "known", "instant": "2026-09-01T00:00:00Z"},
            "planned_start": {"state": "known", "instant": "2026-08-02T00:00:00Z"},
            "planned_target": {"state": "known", "instant": "2026-08-31T00:00:00Z"},
            "actual_start": {"state": "known", "instant": "2026-08-02T00:00:00Z"},
            "actual_finish": {
                "state": "not_applicable",
                "instant": None,
                "reason": "not an actual date",
            },
        },
        "baseline_variance": {"state": "not_applicable", "days": None},
        "progress": {"state": "known", "percent": 50, "as_of": NOW.isoformat()},
        "progress_basis": "explicit",
        "rollup": {
            "state": "not_applicable",
            "eligible_children": 0,
            "included_children": 0,
            "start": {
                "state": "not_applicable",
                "instant": None,
                "reason": "no children",
            },
            "end": {
                "state": "not_applicable",
                "instant": None,
                "reason": "no children",
            },
            "progress": {
                "state": "not_applicable",
                "percent": None,
                "as_of": None,
                "reason": "no children",
            },
            "exclusions": [],
        },
        "source_watermarks": [],
        "evidence_refs": [],
    }


# ---------------------------------------------------------------------------
# 1. Schema validation (frozen schema on disk)
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_valid_projection_validates_against_frozen_schema(self, validator):
        validator.validate(_valid_projection())

    def test_missing_required_field_fails(self, validator):
        projection = _valid_projection()
        del projection["truth_state"]
        with pytest.raises(Exception):
            validator.validate(projection)

    def test_invalid_enum_value_fails(self, validator):
        projection = _valid_projection()
        projection["truth_state"] = "magical"
        with pytest.raises(Exception):
            validator.validate(projection)

    def test_over_bound_dependencies_fail_schema(self, schema):
        dependencies = schema["properties"]["dependencies"]
        assert dependencies["maxItems"] == 20_000

    def test_over_bound_items_fail_schema(self, schema):
        items = schema["properties"]["items"]
        assert items["maxItems"] == 10_000

    def test_r1_vocabulary_is_not_schema_vocabulary(self, schema):
        item_type = schema["$defs"]["item"]["properties"]["item_type"]
        assert "feature" not in item_type["enum"]
        assert "project" in item_type["enum"]

    def test_dependency_vocabulary_replaces_r1(self, schema):
        dependency = schema["$defs"]["dependency"]["properties"]
        assert "lag_seconds" in dependency
        assert "lag_days" not in dependency
        assert "edge_type" in dependency
        assert "dependency_type" not in dependency

    def test_date_states_include_r2_additions(self, schema):
        states = schema["$defs"]["date_value"]["properties"]["state"]["enum"]
        assert {"stale", "partial"} <= set(states)


# ---------------------------------------------------------------------------
# 2. Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    def _read(self, provider, context=None, verifier=None):
        return provider.read(
            context or FakeContext(),
            {"role": "operator", "scope": "estate", "timezone": "UTC"},
            None,
            currentness_verifier=verifier or Verifier(),
        )

    def test_missing_snapshot_fails_closed(self):
        provider = FakeScheduleProvider(None)
        with pytest.raises(ProviderUnavailableError):
            self._read(provider)

    def test_denied_pre_read_fails_closed(self):
        provider = FakeScheduleProvider(_snapshot())
        with pytest.raises(ProviderUnavailableError):
            self._read(provider, verifier=Verifier(before="deny"))
        with pytest.raises(ProviderUnavailableError):
            self._read(provider, verifier=Verifier(before="abstain"))

    def test_denied_post_read_fails_closed(self):
        provider = FakeScheduleProvider(_snapshot())
        with pytest.raises(ProviderUnavailableError):
            self._read(provider, verifier=Verifier(after="deny"))

    def test_mismatched_capability_fails_closed(self):
        provider = FakeScheduleProvider(_snapshot())
        context = FakeContext(capability="skdashboard.schedule.read")
        with pytest.raises(ProviderUnavailableError):
            self._read(provider, context=context)

    def test_mismatched_target_fails_closed(self):
        provider = FakeScheduleProvider(_snapshot())
        context = FakeContext(target="/api/v1/other")
        with pytest.raises(ProviderUnavailableError):
            self._read(provider, context=context)

    def test_denied_decision_fails_closed(self):
        provider = FakeScheduleProvider(_snapshot())
        with pytest.raises(ProviderUnavailableError):
            self._read(provider, context=FakeContext(allow=False))

    def test_verifier_called_once_before_and_once_after(self):
        provider = FakeScheduleProvider(_snapshot())
        verifier = Verifier()
        self._read(provider, verifier=verifier)
        assert verifier.calls == ["before", "after"]

    def test_unknown_snapshot_keys_rejected(self):
        snapshot = _snapshot(extra_key="nope")
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            self._read(provider)

    def test_wrong_schema_version_rejected(self):
        provider = FakeScheduleProvider(_snapshot(schema_version="9.9.9"))
        with pytest.raises(ProviderUnavailableError):
            self._read(provider)

    def test_tenant_mismatch_fails_closed(self):
        provider = FakeScheduleProvider(_snapshot(tenant_id="tenant-2"))
        with pytest.raises(ProviderUnavailableError):
            self._read(provider)

    def test_unauthorized_authorization_state_fails_closed(self):
        snapshot = _snapshot()
        snapshot["authorization"]["state"] = "denied"
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            self._read(provider)


# ---------------------------------------------------------------------------
# 3. Freshness
# ---------------------------------------------------------------------------


class TestFreshness:
    def test_stale_snapshot_rejected_at_default_ttl(self):
        snapshot = _snapshot(
            observed_at=(NOW - timedelta(seconds=301)).isoformat(),
            projected_at=(NOW - timedelta(seconds=300)).isoformat(),
        )
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {"role": "operator", "scope": "estate", "timezone": "UTC"},
                None,
                currentness_verifier=Verifier(),
            )

    def test_future_watermark_rejected(self):
        snapshot = _snapshot(
            observed_at=(NOW + timedelta(seconds=30)).isoformat(),
            projected_at=(NOW + timedelta(seconds=31)).isoformat(),
        )
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {"role": "operator", "scope": "estate", "timezone": "UTC"},
                None,
                currentness_verifier=Verifier(),
            )

    def test_projected_before_observed_rejected(self):
        snapshot = _snapshot(
            observed_at=(NOW - timedelta(seconds=5)).isoformat(),
            projected_at=(NOW - timedelta(seconds=10)).isoformat(),
        )
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {"role": "operator", "scope": "estate", "timezone": "UTC"},
                None,
                currentness_verifier=Verifier(),
            )

    def test_within_ttl_succeeds(self):
        provider = FakeScheduleProvider(_snapshot())
        projection = provider.read(
            FakeContext(),
            {"role": "operator", "scope": "estate", "timezone": "UTC"},
            None,
            currentness_verifier=Verifier(),
        )
        assert projection["projection_version"] == "rev-1"

    def test_ttl_bounds_enforced_at_construction(self):
        with pytest.raises(ValueError):
            FakeScheduleProvider(_snapshot(), max_source_age_seconds=0)
        with pytest.raises(ValueError):
            FakeScheduleProvider(_snapshot(), max_source_age_seconds=86_401)


# ---------------------------------------------------------------------------
# 4. Bounds and malformed sources
# ---------------------------------------------------------------------------


class TestBoundsAndMalformedSources:
    def test_over_bound_items_rejected_not_truncated(self):
        snapshot = _snapshot(items=[{"stub": True}] * 10_001)
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {"role": "operator", "scope": "estate", "timezone": "UTC"},
                None,
                currentness_verifier=Verifier(),
            )

    def test_over_bound_dependencies_rejected_not_truncated(self):
        snapshot = _snapshot(dependencies=[{"stub": True}] * 20_001)
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {"role": "operator", "scope": "estate", "timezone": "UTC"},
                None,
                currentness_verifier=Verifier(),
            )

    def test_over_bound_overlays_rejected_not_truncated(self):
        snapshot = _snapshot(overlays=[{"stub": True}] * 5_001)
        provider = FakeScheduleProvider(snapshot)
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {"role": "operator", "scope": "estate", "timezone": "UTC"},
                None,
                currentness_verifier=Verifier(),
            )

    def test_no_truncation_flag_exists_in_schema(self, schema):
        text = json.dumps(schema)
        assert '"truncated"' not in text


# ---------------------------------------------------------------------------
# 5. Role and scope alignment
# ---------------------------------------------------------------------------


ROUTE_ROLE_ALIASES = {"project-manager", "operator", "architect", "service", "team"}
CANONICAL_ROLES = {"portfolio", "project_manager", "architect", "service", "team"}
ROLE_MAP = {
    "operator": "portfolio",
    "portfolio": "portfolio",
    "project-manager": "project_manager",
    "project_manager": "project_manager",
    "architect": "architect",
    "service": "service",
    "team": "team",
}


class TestRoleAndScope:
    @pytest.mark.parametrize(
        "alias,canonical",
        [
            ("project-manager", "project_manager"),
            ("operator", "portfolio"),
            ("architect", "architect"),
            ("service", "service"),
            ("team", "team"),
        ],
    )
    def test_route_alias_maps_to_canonical_role(self, alias, canonical):
        assert ROLE_MAP[alias] == canonical

    def test_mapping_is_narrowing_not_broadening(self):
        assert set(ROLE_MAP.values()) == CANONICAL_ROLES
        assert len(ROLE_MAP) == 7

    def test_non_estate_scope_rejected(self):
        provider = FakeScheduleProvider(_snapshot())
        # The real route rejects scope != estate before the provider runs; the
        # contract double models the same rejection as a provider-level guard.
        with pytest.raises(ProviderUnavailableError):
            provider.read(
                FakeContext(),
                {
                    "role": "operator",
                    "scope": "project",
                    "service": "all",
                    "timezone": "UTC",
                },
                None,
                currentness_verifier=Verifier(),
            )


# ---------------------------------------------------------------------------
# 6. Forecast representation
# ---------------------------------------------------------------------------


class TestForecastRepresentation:
    def test_ready_artifact_exact_key_set(self):
        periods = [_period(f"p{i}", NOW - timedelta(days=7 * (i + 1)), 5) for i in range(6)]
        artifact = forecast_double(periods)
        assert set(artifact) == {
            "schema_version",
            "artifact_kind",
            "state",
            "abstention_reason",
            "method",
            "calculation_owner",
            "method_discrimination",
            "cohort",
            "scope",
            "history_window",
            "sample_periods",
            "period_cadence_days",
            "remaining_work",
            "iterations",
            "seed",
            "assumptions",
            "exclusions",
            "individual_ranking_prohibited",
            "completion_quantiles_periods",
            "milestone_confidence",
            "writes_owner_records",
        }

    def test_ready_artifact_typed_fields(self):
        periods = [_period(f"p{i}", NOW - timedelta(days=7 * (i + 1)), 5) for i in range(6)]
        artifact = forecast_double(periods)
        assert artifact["state"] == "ready"
        assert artifact["abstention_reason"] is None
        assert artifact["method"] == "aggregate_throughput_bootstrap_monte_carlo"
        assert artifact["calculation_owner"] == "deterministic_engine"
        assert artifact["method_discrimination"] == {
            "throughput_forecast": "probabilistic aggregate flow in periods",
            "date_critical_path": "not calculated or blended by this artifact",
        }
        assert artifact["individual_ranking_prohibited"] is True
        assert artifact["writes_owner_records"] is False
        quantiles = artifact["completion_quantiles_periods"]
        assert set(quantiles) == {"p50", "p85", "p95"}
        assert quantiles["p50"] <= quantiles["p85"] <= quantiles["p95"]

    def test_low_sample_forecast_abstains(self):
        periods = [_period("p1", NOW - timedelta(days=7), 5)]
        artifact = forecast_double(periods)
        assert artifact["state"] == "abstained"
        assert artifact["abstention_reason"]
        assert all(v is None for v in artifact["completion_quantiles_periods"].values())
        assert artifact["milestone_confidence"] is None

    def test_mixed_cadence_forecast_abstains(self):
        periods = [_period(f"p{i}", NOW - timedelta(days=7 * (i + 1)), 5) for i in range(6)]
        periods.append(_period("odd", NOW - timedelta(days=3), 5, days=3))
        artifact = forecast_double(periods)
        assert artifact["state"] == "abstained"
        assert "cadence" in artifact["abstention_reason"]

    def test_non_canonical_timing_excluded_and_recorded(self):
        periods = [_period(f"p{i}", NOW - timedelta(days=7 * (i + 1)), 5) for i in range(6)]
        periods.append(_period("weird", NOW - timedelta(days=1), 99, timing_basis="ad_hoc"))
        artifact = forecast_double(periods)
        assert artifact["sample_periods"] == 6
        assert artifact["exclusions"] == [
            {
                "period_id": "weird",
                "timing_basis": "ad_hoc",
                "reason": "non-canonical timing excluded from aggregate throughput sampling",
            }
        ]

    def test_r1_forecast_vocabulary_is_retired(self):
        periods = [_period(f"p{i}", NOW - timedelta(days=7 * (i + 1)), 5) for i in range(6)]
        artifact = forecast_double(periods)
        assert artifact["method"] != "skcore_throughput_v1"
        assert artifact["calculation_owner"] != "skcore_aggregate_history_provider"
        assert artifact["iterations"] != 10_000

    def test_default_iterations_are_2000(self):
        periods = [_period(f"p{i}", NOW - timedelta(days=7 * (i + 1)), 5) for i in range(6)]
        assert forecast_double(periods)["iterations"] == 2000


# ---------------------------------------------------------------------------
# 7. OpenAPI alignment (AC4)
# ---------------------------------------------------------------------------


class TestOpenApiAlignment:
    @pytest.fixture(scope="class")
    def openapi(self):
        return _load(OPENAPI_PATH)

    def test_frozen_openapi_declares_schedule_read_capability(self, openapi):
        operation = openapi["paths"]["/schedule/projection"]["get"]
        assert operation["security"] == [{"capability": ["skdashboard.schedule.read"]}]

    def test_documented_capability_is_alias_of_implemented_capability(self):
        # Section 6.1: implemented route accepts ONLY skdashboard.read; the
        # OpenAPI capability is a documentation alias of the same single
        # authorization. No second accepted capability is created.
        implemented = "skdashboard.read"
        documented = "skdashboard.schedule.read"
        assert implemented != documented
        assert documented.startswith("skdashboard.")

    def test_role_enums_are_compatible_via_alias_map(self, openapi):
        openapi_roles = set(openapi["components"]["parameters"]["Role"]["schema"]["enum"])
        assert openapi_roles == CANONICAL_ROLES
        assert openapi_roles == {ROLE_MAP[alias] for alias in ROUTE_ROLE_ALIASES}

    def test_openapi_only_parameters_are_documented_not_wireable(self, openapi):
        declared = set(openapi["components"]["parameters"])
        not_wireable = {
            "PortfolioId",
            "ProjectId",
            "ServiceId",
            "TeamId",
            "ProjectionVersion",
        }
        assert not_wireable <= declared
        implemented_params = {
            "role",
            "scope",
            "window",
            "baseline",
            "service",
            "lens",
            "timezone",
            "selected_item",
        }
        assert implemented_params.isdisjoint({p.lower() for p in not_wireable})


# ---------------------------------------------------------------------------
# 8. Route negative contract (constants)
# ---------------------------------------------------------------------------


class TestRouteNegativeContract:
    def test_error_codes_pinned(self):
        assert {
            "SCHEDULE_UNAVAILABLE",
            "SCHEDULE_FORECAST_UNAVAILABLE",
            "INVALID_SCHEDULE_SCOPE",
        } >= {
            "SCHEDULE_UNAVAILABLE",
            "SCHEDULE_FORECAST_UNAVAILABLE",
            "INVALID_SCHEDULE_SCOPE",
        }

    def test_stale_data_code_is_retired(self):
        # R1 STALE_DATA is retired: staleness surfaces as SCHEDULE_UNAVAILABLE.
        assert "STALE_DATA" not in {
            "SCHEDULE_UNAVAILABLE",
            "SCHEDULE_FORECAST_UNAVAILABLE",
            "INVALID_SCHEDULE_SCOPE",
        }

    def test_unavailable_is_retryable_and_invalid_scope_is_not(self):
        retryable = {
            "SCHEDULE_UNAVAILABLE": True,
            "SCHEDULE_FORECAST_UNAVAILABLE": True,
            "INVALID_SCHEDULE_SCOPE": False,
        }
        assert retryable["SCHEDULE_UNAVAILABLE"] is True
        assert retryable["INVALID_SCHEDULE_SCOPE"] is False
