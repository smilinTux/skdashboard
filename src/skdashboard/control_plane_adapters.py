"""Bounded, read-only cross-estate observation adapters."""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Callable, Mapping

ADAPTER_VERSION = "1.0.0"
SCHEMA_VERSION = "1.1.0"
MAX_SOURCE_BYTES = 1_048_576
MAX_SOURCE_ITEMS = 2_048


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _observed_at(value: os.stat_result) -> str:
    return datetime.fromtimestamp(value.st_mtime, timezone.utc).isoformat()


def _read_json_snapshot(path: Path) -> tuple[object, str, str, tuple[int, int, int, int, int]]:
    """Read one bounded JSON snapshot and reject an in-place concurrent change."""
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size > MAX_SOURCE_BYTES:
            raise ValueError("observation exceeds byte limit")
        raw = source.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(source.fileno())
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("observation exceeds byte limit")
    signature = _stat_signature(before)
    if _stat_signature(after) != signature:
        raise RuntimeError("source changed during read")
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), _observed_at(before), signature


def _read_json_bounded(path: Path) -> object:
    return _read_json_snapshot(path)[0]


def _directory_snapshot(path: Path, pattern: str | None = None) -> tuple[list[Path], str]:
    """Enumerate one bounded directory snapshot and reject concurrent mutation."""
    before = path.stat()
    entries = list(islice(path.glob(pattern) if pattern else path.iterdir(), MAX_SOURCE_ITEMS + 1))
    after = path.stat()
    if len(entries) > MAX_SOURCE_ITEMS:
        raise ValueError("directory exceeds item limit")
    if _stat_signature(before) != _stat_signature(after):
        raise RuntimeError("source changed during read")
    return entries, _observed_at(before)


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    owner: str
    population: str
    fields: tuple[str, ...]
    ttl_seconds: int = 60
    timeout_ms: int = 1_000
    classification: str = "internal"


SPECS = (
    AdapterSpec(
        "skcapstone.portfolio",
        "SKCapstone",
        "portfolio_project_work",
        ("total", "open", "in_progress", "done"),
    ),
    AdapterSpec(
        "skcoord.flow", "skcoord", "task_flow", ("open", "in_progress", "done", "blocked")
    ),
    AdapterSpec(
        "skcoord.agent_presence",
        "skcoord",
        "agent_presence",
        ("total_agents", "active_agents"),
    ),
    AdapterSpec(
        "skcapstone.itil",
        "SKCapstone ITIL",
        "itil_records",
        ("open_incidents", "sev1", "sev2", "awaiting_cab"),
    ),
    AdapterSpec(
        "skcapstone.service_release",
        "SKCapstone",
        "service_release_observations",
        ("services", "releases"),
    ),
    AdapterSpec(
        "cmdb.configuration",
        "CMDB",
        "configuration_items",
        ("total", "operational", "degraded", "other_status", "fresh", "stale", "unknown"),
    ),
    AdapterSpec(
        "skcapstone.fleet",
        "SKCapstone Fleet",
        "fleet_runtime",
        ("graded", "skipped", "error", "warn", "info", "ok"),
    ),
    AdapterSpec(
        "skcounter.harness",
        "SKCounter",
        "harness_reported",
        (
            "tokens_total",
            "cost_usd",
            "cost_state",
            "observation_count",
            "fresh_collectors",
            "delayed_collectors",
            "stale_collectors",
        ),
    ),
    AdapterSpec(
        "skgateway.observed",
        "SKGateway",
        "gateway_observed",
        (
            "tokens_total",
            "cost_usd",
            "cost_state",
            "observation_count",
            "fresh_collectors",
            "delayed_collectors",
            "stale_collectors",
        ),
    ),
    AdapterSpec("skperf.aggregate", "SKPerf", "approved_benchmarks", ("regressions", "capacity_pressure")),
    AdapterSpec("skjoule.wallet", "SKJoule", "wallets", ("total_supply", "active_agents")),
    AdapterSpec("capauth.policy", "CapAuth", "policy_health", ("available", "denials"), classification="confidential"),
    AdapterSpec("atlas.conditions", "Atlas", "operator_conditions", ("open_conditions", "ready_actions"), classification="confidential"),
    AdapterSpec("skos.discovery", "SKOS", "module_discovery", ("discovered", "unavailable")),
    AdapterSpec("sklegal.global", "SKLegal", "policy_filtered_global_aggregate", ("matters", "deadline_pressure"), classification="confidential"),
    AdapterSpec("hammertime.pipeline", "HammerTime", "approved_aggregate_pipeline", ("approved_releases", "pipeline_failures"), classification="confidential"),
)

@dataclass(frozen=True)
class Reader:
    payload: dict | None = None
    adapter_id: str | None = None
    home: Path | None = None
    timeout_ms: int | None = None
    failure: str | None = None

    def __call__(self) -> dict:
        failures = {
            "timeout": TimeoutError,
            "unreachable": ConnectionError,
            "unauthorized": PermissionError,
            "unavailable": RuntimeError,
        }
        if self.failure in failures:
            raise failures[self.failure]
        if self.payload is not None:
            return self.payload
        if self.adapter_id and self.home is not None and self.timeout_ms is not None:
            return _subprocess_read(self.adapter_id, self.home, self.timeout_ms)
        raise RuntimeError


def _iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _error(
    spec: AdapterSpec,
    projected_at: str,
    code: str,
    message: str,
    *,
    truth_state: str = "unavailable",
    visibility: dict | None = None,
) -> dict:
    visibility = (
        _visibility(spec)
        if spec.adapter_id == "sklegal.global"
        else (visibility or _visibility(spec))
    )
    return {
        "adapter_id": spec.adapter_id,
        "adapter_version": ADAPTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "owner": spec.owner,
        "population": spec.population,
        "classification": spec.classification,
        "visibility": visibility,
        "query_budget": {"max_items": 1, "timeout_ms": spec.timeout_ms},
        "ttl_seconds": spec.ttl_seconds,
        "age_seconds": None,
        "observed_at": None,
        "projected_at": projected_at,
        "watermark": {"source": spec.adapter_id, "value": None},
        "truth_state": truth_state,
        "coverage": {"expected": None, "reporting": None},
        "aggregate": None,
        "errors": [{"code": code, "message": message, "retryable": True}],
    }


def _visibility(spec: AdapterSpec) -> dict:
    if spec.adapter_id == "sklegal.global":
        return {
            "state": "policy_filtered",
            "authorization": "unknown",
            "reason": "Tenant and Matter policy was not evaluated at global scope",
        }
    return {"state": "visible", "authorization": "authorized"}


def _project(spec: AdapterSpec, reader: Reader | None, now: datetime | None) -> dict:
    def projected_at() -> str:
        instant = now or datetime.now(timezone.utc)
        return instant.isoformat().replace("+00:00", "Z")

    if reader is None or not isinstance(reader, Reader):
        return _error(
            spec,
            projected_at(),
            "SOURCE_UNAVAILABLE",
            "No authorized aggregate reader is configured",
        )

    try:
        raw = reader()
    except PermissionError:
        return _error(
            spec,
            projected_at(),
            "SOURCE_UNAUTHORIZED",
            "The aggregate reader is not authorized",
            truth_state="unknown",
            visibility={"state": "unauthorized", "authorization": "denied"},
        )
    except ConnectionError:
        return _error(
            spec,
            projected_at(),
            "SOURCE_UNREACHABLE",
            "The aggregate reader is unreachable",
            truth_state="unreachable",
        )
    except TimeoutError:
        return _error(
            spec,
            projected_at(),
            "SOURCE_TIMEOUT",
            "The aggregate reader exceeded its query budget",
        )
    except ValueError:
        return _error(
            spec,
            projected_at(),
            "SOURCE_MALFORMED",
            "The aggregate reader returned malformed data",
        )
    except Exception:  # noqa: BLE001 - source failures are masked at this boundary
        return _error(
            spec,
            projected_at(),
            "SOURCE_UNAVAILABLE",
            "The aggregate reader is unavailable",
        )
    projected = projected_at()
    instant = now or datetime.now(timezone.utc)
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return _error(
            spec,
            projected,
            "SOURCE_MALFORMED",
            "The aggregate reader returned an incompatible schema",
        )

    observed = _iso(raw.get("observed_at"))
    aggregate = raw.get("aggregate")
    coverage = raw.get("coverage")
    watermark = raw.get("watermark")
    errors = raw.get("errors", [])
    has_observations = raw.get("has_observations")
    if (
        set(raw)
        != {
            "schema_version",
            "observed_at",
            "watermark",
            "coverage",
            "aggregate",
            "errors",
            "has_observations",
        }
        or not isinstance(errors, list)
        or len(errors) > 16
        or observed is None
        or not isinstance(aggregate, dict)
        or set(aggregate) != set(spec.fields)
        or any(
            not isinstance(value, (str, int, float, bool, type(None)))
            or (isinstance(value, str) and len(value) > 128)
            or (isinstance(value, float) and not math.isfinite(value))
            for value in aggregate.values()
        )
        or not isinstance(coverage, dict)
        or set(coverage) != {"expected", "reporting"}
        or not all(
            value is None or (isinstance(value, int) and not isinstance(value, bool))
            for value in coverage.values()
        )
        or not isinstance(watermark, str)
        or not watermark
        or len(watermark) > 256
        or any(not isinstance(value, str) for value in errors)
        or not isinstance(has_observations, bool)
        or len(json.dumps(aggregate, separators=(",", ":"), default=str).encode()) > 4_096
    ):
        return _error(
            spec, projected, "SOURCE_MALFORMED", "The aggregate reader returned malformed data"
        )

    expected = coverage["expected"]
    reporting = coverage["reporting"]
    if expected is not None and reporting is not None and (expected < 0 or reporting < 0 or reporting > expected):
        return _error(
            spec, projected, "SOURCE_MALFORMED", "The aggregate reader returned invalid coverage"
        )

    age = (instant - observed).total_seconds()
    if age < -300:
        return _error(
            spec, projected, "SOURCE_MALFORMED", "The source observation is in the future"
        )
    if errors and not has_observations:
        return _error(
            spec,
            projected,
            "SOURCE_UNAVAILABLE",
            "The aggregate source returned no usable evidence",
        )

    age_seconds = max(0, int(age))
    truth_state = "current" if has_observations else "unknown"
    if errors or (expected is not None and reporting is not None and reporting < expected):
        truth_state = "partial"
    elif has_observations and age_seconds > spec.ttl_seconds:
        truth_state = "stale"

    result = {
        "adapter_id": spec.adapter_id,
        "adapter_version": ADAPTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "owner": spec.owner,
        "population": spec.population,
        "classification": spec.classification,
        "visibility": _visibility(spec),
        "query_budget": {"max_items": 1, "timeout_ms": spec.timeout_ms},
        "ttl_seconds": spec.ttl_seconds,
        "age_seconds": age_seconds,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "projected_at": projected,
        "watermark": {"source": spec.adapter_id, "value": watermark},
        "truth_state": truth_state,
        "coverage": coverage,
        "aggregate": (
            {key: aggregate[key] for key in spec.fields}
            if truth_state != "unknown"
            else None
        ),
        "errors": [
            {"code": "SOURCE_PARTIAL", "message": "The aggregate reader reported partial evidence", "retryable": True}
            for _ in errors[:1]
        ],
    }
    return result


def project_estate(readers: Mapping[str, Reader], *, now: datetime | None = None) -> list[dict]:
    """Project one bounded, policy-safe aggregate for every declared population."""
    instant = now.astimezone(timezone.utc) if now else None
    with ThreadPoolExecutor(max_workers=len(SPECS)) as executor:
        futures = [
            executor.submit(_project, spec, readers.get(spec.adapter_id), instant)
            for spec in SPECS
        ]
        return [future.result() for future in futures]


def aggregate_reader(
    aggregate: dict,
    *,
    expected: int | None = 1,
    reporting: int | None = 1,
    observed_at: str | None = None,
    errors: list[str] | None = None,
    has_observations: bool = True,
    watermark_data: object | None = None,
) -> Reader:
    """Wrap an already bounded aggregate in the adapter input contract."""
    safe = json.dumps(
        {
            "aggregate": aggregate,
            "coverage": {"expected": expected, "reporting": reporting},
            "errors": errors or [],
            "has_observations": has_observations,
            "source": watermark_data,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    watermark = f"sha256:{hashlib.sha256(safe.encode()).hexdigest()}"
    timestamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return Reader(
        payload={
            "schema_version": SCHEMA_VERSION,
            "observed_at": timestamp,
            "watermark": watermark,
            "coverage": {"expected": expected, "reporting": reporting},
            "aggregate": aggregate,
            "errors": list(errors or []),
            "has_observations": has_observations,
        }
    )


def _local_readers(
    home: Path, *, board_data: dict, default_observed_at: str | None = None
) -> dict[str, Callable[[], dict]]:
    """Reuse existing read models without crossing protected source boundaries."""
    readers: dict[str, Callable[[], dict]] = {}
    summary = board_data.get("summary")
    tasks = board_data.get("tasks")
    agents = board_data.get("agents")
    if (
        not board_data.get("error")
        and isinstance(summary, dict)
        and all(isinstance(summary.get(key), int) for key in ("total", "open", "in_progress", "done"))
        and isinstance(tasks, list)
        and isinstance(agents, list)
        and all(isinstance(task, dict) and isinstance(task.get("status"), str) for task in tasks)
        and all(isinstance(agent, dict) and isinstance(agent.get("state"), str) for agent in agents)
    ):
        readers["skcapstone.portfolio"] = aggregate_reader(
            {key: summary.get(key, 0) for key in ("total", "open", "in_progress", "done")},
            observed_at=default_observed_at,
        )
        readers["skcoord.flow"] = aggregate_reader({
            "open": summary.get("open", 0),
            "in_progress": summary.get("in_progress", 0),
            "done": summary.get("done", 0),
            "blocked": sum(task.get("status") == "blocked" for task in tasks),
        }, observed_at=default_observed_at)
        readers["skcoord.agent_presence"] = aggregate_reader({
            "total_agents": len(agents),
            "active_agents": sum(agent.get("state") == "active" for agent in agents),
        }, observed_at=default_observed_at)

    from . import dashboard_cmdb, dashboard_fleet, dashboard_itil, dashboard_skcounter

    def itil() -> dict:
        raw = dashboard_itil.get_overview(home)
        if raw.get("error"):
            raise RuntimeError
        kpis = raw.get("kpis")
        activity = raw.get("activity")
        fields = ("open_incidents", "sev1", "sev2", "awaiting_cab")
        if not isinstance(kpis, dict) or not all(key in kpis for key in fields) or not isinstance(activity, list):
            raise ValueError
        observed_at = max(
            (item.get("ts") for item in activity if item.get("ts")),
            default=None,
        )
        return aggregate_reader(
            {
                key: kpis.get(key)
                for key in fields
            },
            observed_at=observed_at,
            watermark_data=observed_at,
            has_observations=bool(activity),
        )()

    def cmdb() -> dict:
        raw = dashboard_cmdb.get_overview(home)
        health = raw.get("health")
        freshness = raw.get("evidence_health")
        required_freshness = ("fresh", "stale", "unknown", "unreachable")
        if (
            not isinstance(raw.get("total"), int)
            or not isinstance(health, dict)
            or not all(isinstance(value, int) for value in health.values())
            or not isinstance(freshness, dict)
            or not all(isinstance(freshness.get(key), int) for key in required_freshness)
        ):
            raise ValueError
        stale = freshness.get("stale", 0)
        unknown = freshness.get("unknown", 0) + freshness.get("unreachable", 0)
        reconciliation = raw.get("last_successful_reconciliation")
        return aggregate_reader(
            {
                "total": raw.get("total", 0),
                "operational": health.get("operational", 0),
                "degraded": health.get("degraded", 0),
                "other_status": sum(
                    value
                    for key, value in health.items()
                    if key not in {"operational", "degraded"}
                ),
                "fresh": freshness.get("fresh", 0),
                "stale": stale,
                "unknown": unknown,
            },
            errors=["partial"] if stale or unknown else [],
            has_observations=bool(raw.get("total")) and bool(reconciliation),
            observed_at=reconciliation,
            watermark_data=reconciliation,
        )()

    def fleet() -> dict:
        raw = dashboard_fleet.get_drift(home, alert=False)
        summary = raw.get("summary")
        fields = ("graded", "skipped", "error", "warn", "info", "ok")
        if (
            not isinstance(summary, dict)
            or not all(isinstance(summary.get(key), int) for key in fields)
            or not isinstance(raw.get("errors"), list)
        ):
            raise ValueError
        return aggregate_reader(
            {key: summary[key] for key in fields},
            expected=summary.get("graded", 0) + summary.get("skipped", 0),
            reporting=summary.get("graded", 0),
            errors=["partial"] if raw.get("errors") else [],
            has_observations=bool(summary["graded"] or summary["skipped"]),
            observed_at=default_observed_at,
        )()

    def usage(lane: str) -> dict:
        raw = dashboard_skcounter.get_ai_usage(home, {"lane": lane})
        summary = raw.get("summary")
        coverage = raw.get("coverage")
        collectors = raw.get("collectors")
        required_coverage = (
            "expected_nodes",
            "reporting_nodes",
            "fresh_collectors",
            "delayed_collectors",
            "stale_collectors",
        )
        if (
            not isinstance(summary, dict)
            or "total" not in summary
            or "cost_state" not in summary
            or not isinstance(coverage, dict)
            or not all(isinstance(coverage.get(key), int) for key in required_coverage)
            or not isinstance(collectors, list)
            or not isinstance(raw.get("observation_count"), int)
            or not isinstance(raw.get("errors"), list)
        ):
            raise ValueError
        observed = min(
            (item.get("last_seen") for item in collectors if item.get("last_seen")),
            default=raw.get("generated_at"),
        )
        stale_collectors = coverage.get("stale_collectors", 0)
        delayed_collectors = coverage.get("delayed_collectors", 0)
        collector_states = {
            item.get("status") for item in collectors if item.get("status")
        }
        return aggregate_reader(
            {
                "tokens_total": summary.get("total", 0),
                "cost_usd": summary.get("cost_usd") if summary.get("cost_state") == "available" else None,
                "cost_state": summary.get("cost_state", "unavailable"),
                "observation_count": raw.get("observation_count", 0),
                "fresh_collectors": coverage["fresh_collectors"],
                "delayed_collectors": delayed_collectors,
                "stale_collectors": stale_collectors,
            },
            expected=coverage.get("expected_nodes"),
            reporting=coverage.get("reporting_nodes"),
            observed_at=observed,
            errors=["partial"] if raw.get("errors") or len(collector_states) > 1 else [],
            has_observations=bool(raw.get("observation_count")),
            watermark_data=collectors,
        )()

    def joule() -> dict:
        from skcapstone.skjoule import JouleEngine

        stats = JouleEngine(home=home).get_network_stats()
        balances = stats.agent_balances
        if not isinstance(balances, dict) or not isinstance(stats.active_agents, int):
            raise ValueError
        return aggregate_reader(
            {
                "total_supply": sum(balances.values()),
                "active_agents": stats.active_agents,
            },
            has_observations=bool(balances),
            observed_at=default_observed_at,
        )()

    def service_release() -> dict:
        """Read service release observations from CMDB service CIs with release metadata."""
        try:
            from skcoord.cmdb import CMDBManager

            manager = CMDBManager(home.expanduser())
            all_cis = manager.list_cis()
            service_cis = [ci for ci in all_cis[:MAX_SOURCE_ITEMS] if ci.ci_type == "service"]

            services_count = len(service_cis)
            releases_count = 0
            errors = []

            for ci in service_cis:
                if ci.attributes.get("release_version") or ci.attributes.get("deployed_at"):
                    releases_count += 1
                if not ci.owner:
                    errors.append("service_without_owner")

            has_observations = services_count > 0

            return aggregate_reader(
                {
                    "services": services_count,
                    "releases": releases_count,
                },
                expected=services_count,
                reporting=services_count,
                errors=errors[:1] if errors else [],
                has_observations=has_observations,
                observed_at=default_observed_at,
                watermark_data=f"cmdb-service-fold:{len(service_cis)}",
            )()
        except PermissionError:
            raise
        except Exception:
            raise RuntimeError

    def skperf_aggregate() -> dict:
        """Read approved benchmark aggregates from SKPerf data when available."""
        perf_home = home / "skperf"
        perf_data_path = perf_home / "data" / "aggregate.json"

        try:
            if not perf_data_path.exists():
                raise RuntimeError("SKPerf aggregate data unavailable")

            perf_data = _read_json_bounded(perf_data_path)

            if not isinstance(perf_data, dict):
                raise ValueError("SKPerf data malformed")

            regressions = perf_data.get("regressions", 0)
            capacity_pressure = perf_data.get("capacity_pressure", 0.0)
            reporting = perf_data.get("reporting_benchmarks", 0)
            expected = perf_data.get("expected_benchmarks", reporting)
            observed_at = perf_data.get("observed_at", default_observed_at)
            errors = perf_data.get("errors", [])

            if not isinstance(regressions, int) or not isinstance(capacity_pressure, (int, float)):
                raise ValueError("SKPerf aggregate fields malformed")

            return aggregate_reader(
                {
                    "regressions": regressions,
                    "capacity_pressure": float(capacity_pressure) if isinstance(capacity_pressure, (int, float)) else 0.0,
                },
                expected=expected,
                reporting=reporting,
                errors=errors[:1] if errors else [],
                has_observations=reporting > 0,
                observed_at=observed_at,
                watermark_data=perf_data_path.name,
            )()
        except PermissionError:
            raise
        except RuntimeError:
            raise
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError("SKPerf data malformed") from e
        except Exception as e:
            raise RuntimeError("SKPerf aggregate data unavailable") from e

    def capauth_policy() -> dict:
        """Read CapAuth policy health from estate and policy check state."""
        try:
            from capauth.estate import EstateManifest

            capauth_home = home / "capauth"
            estate_path = capauth_home / "estate.json"
            if not estate_path.exists():
                raise RuntimeError("CapAuth estate unavailable")

            _, digest, observed_at, signature = _read_json_snapshot(estate_path)
            manifest = EstateManifest.load(estate_path)
            if manifest.digest != digest or _stat_signature(estate_path.stat()) != signature:
                raise RuntimeError("CapAuth estate changed during validation")

            identities = tuple(manifest.identities.values())
            available = bool(identities)
            denials = sum(identity.status != "active" for identity in identities)

            errors = []
            if not available:
                errors.append("no_active_identities")

            has_observations = bool(identities)

            return aggregate_reader(
                {
                    "available": available,
                    "denials": denials,
                },
                expected=len(identities),
                reporting=sum(identity.status == "active" for identity in identities),
                errors=errors[:1] if errors else [],
                has_observations=has_observations,
                observed_at=observed_at,
                watermark_data=f"capauth-estate:{manifest.digest}",
            )()
        except PermissionError:
            raise
        except FileNotFoundError:
            raise RuntimeError("CapAuth policy data unavailable")
        except (json.JSONDecodeError, ValueError):
            raise ValueError("CapAuth policy data malformed")
        except Exception:
            raise RuntimeError

    def atlas_conditions() -> dict:
        """Read Atlas operator conditions from the operator seat observation store."""
        try:

            operator_observations_path = home / "fleet" / "atlas" / "brief" / "brief.json"
            fleet_observations_path = home / "fleet" / "observations" / "conditions.json"

            observations_data = None
            source_path = None
            source_observed_at = None

            if operator_observations_path.exists():
                source_path = operator_observations_path
                observations_data, _, source_observed_at, _ = _read_json_snapshot(
                    operator_observations_path
                )
            elif fleet_observations_path.exists():
                source_path = fleet_observations_path
                observations_data, _, source_observed_at, _ = _read_json_snapshot(
                    fleet_observations_path
                )
            else:
                open_conditions = 0
                ready_actions = 0
                return aggregate_reader(
                    {
                        "open_conditions": open_conditions,
                        "ready_actions": ready_actions,
                    },
                    expected=0,
                    reporting=0,
                    errors=["no_observations_file"],
                    has_observations=False,
                    observed_at=default_observed_at,
                    watermark_data="no_source",
                )()

            if not isinstance(observations_data, dict):
                raise ValueError("Atlas observations data malformed")

            conditions = observations_data.get("conditions", [])
            if not isinstance(conditions, list):
                raise ValueError("Atlas conditions field malformed")

            open_conditions = 0
            ready_actions = 0
            errors = []

            for condition in conditions:
                if not isinstance(condition, dict):
                    continue
                status = condition.get("status", "Unknown").lower()
                if status in {"open", "degraded", "failed"}:
                    open_conditions += 1
                if condition.get("ready_for_action") is True:
                    ready_actions += 1

                if status == "unknown":
                    errors.append("unknown_condition_state")

            has_observations = len(conditions) > 0

            return aggregate_reader(
                {
                    "open_conditions": open_conditions,
                    "ready_actions": ready_actions,
                },
                expected=len(conditions),
                reporting=len([c for c in conditions if isinstance(c, dict) and c.get("status") != "Unknown"]),
                errors=errors[:1] if errors else [],
                has_observations=has_observations,
                observed_at=observations_data.get("observed_at", source_observed_at),
                watermark_data=source_path.name if source_path else "unknown",
            )()
        except PermissionError:
            raise
        except (json.JSONDecodeError, ValueError):
            raise ValueError("Atlas observations data malformed")
        except Exception:
            raise RuntimeError

    def skos_discovery() -> dict:
        """Read SKOS module discovery from skcode arena or manifest registry."""
        try:

            skcode_arena_path = home / "skcode" / "arena"
            skcapstone_repo_path = home / "repo"

            discovered = 0
            unavailable = 0
            errors = []
            observed_at = []

            if skcode_arena_path.exists() and skcode_arena_path.is_dir():
                try:
                    arena_entries, arena_observed_at = _directory_snapshot(skcode_arena_path)
                    observed_at.append(arena_observed_at)
                    discovered = sum(1 for entry in arena_entries if entry.is_dir())
                except PermissionError:
                    raise
                except OSError:
                    errors.append("arena_read_failed")

            if skcapstone_repo_path.exists():
                try:
                    src_path = skcapstone_repo_path / "src" / "skcapstone"
                    if src_path.exists() and src_path.is_dir():
                        module_files, repo_observed_at = _directory_snapshot(src_path, "*.py")
                        observed_at.append(repo_observed_at)
                        discovered += len(module_files)
                except PermissionError:
                    raise
                except OSError:
                    errors.append("repo_scan_failed")

            if discovered == 0:
                unavailable = 1
                errors.append("no_modules_discovered")

            has_observations = discovered > 0

            return aggregate_reader(
                {
                    "discovered": discovered,
                    "unavailable": unavailable,
                },
                expected=discovered + unavailable,
                reporting=discovered,
                errors=errors[:1] if errors else [],
                has_observations=has_observations,
                observed_at=min(observed_at, default=default_observed_at),
                watermark_data=f"skos-scan:{discovered}:{unavailable}",
            )()
        except PermissionError:
            raise
        except Exception:
            raise RuntimeError

    readers.update({
        "skcapstone.itil": itil,
        "cmdb.configuration": cmdb,
        "skcapstone.fleet": fleet,
        "skcounter.harness": lambda: usage("harness_reported"),
        "skgateway.observed": lambda: usage("gateway_observed"),
        "skjoule.wallet": joule,
        "skcapstone.service_release": service_release,
        "skperf.aggregate": skperf_aggregate,
        "capauth.policy": capauth_policy,
        "atlas.conditions": atlas_conditions,
        "skos.discovery": skos_discovery,
    })
    return readers


_IMPLEMENTED = {
    "skcapstone.portfolio",
    "skcoord.flow",
    "skcoord.agent_presence",
    "skcapstone.itil",
    "cmdb.configuration",
    "skcapstone.fleet",
    "skcounter.harness",
    "skgateway.observed",
    "skjoule.wallet",
    "skcapstone.service_release",
    "skperf.aggregate",
    "capauth.policy",
    "atlas.conditions",
    "skos.discovery",
}


def _read_source(adapter_id: str, home: Path) -> dict:
    from .dashboard import _get_board_state

    board_ids = {"skcapstone.portfolio", "skcoord.flow", "skcoord.agent_presence"}
    board_data = _get_board_state(home) if adapter_id in board_ids else {}
    readers = _local_readers(home, board_data=board_data)
    reader = readers.get(adapter_id)
    if reader is None:
        raise LookupError(adapter_id)
    return reader()


def _subprocess_read(adapter_id: str, home: Path, timeout_ms: int) -> dict:
    environment = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (package_root, environment.get("PYTHONPATH")) if value
    )
    completed = _bounded_run(
        [sys.executable, "-m", __name__, "--worker", adapter_id, str(home)],
        timeout_ms=timeout_ms,
        environment=environment,
    )
    failures = {4: ValueError, 5: PermissionError, 6: ConnectionError, 7: ValueError}
    if completed.returncode in failures:
        raise failures[completed.returncode]
    if completed.returncode != 0:
        raise RuntimeError
    if len(completed.stdout.encode()) > 8_192:
        raise ValueError
    raw = json.loads(completed.stdout)
    if not isinstance(raw, dict):
        raise ValueError
    return raw


def _bounded_run(
    command: list[str], *, timeout_ms: int, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_ms / 1_000)
    except subprocess.TimeoutExpired as exc:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.communicate()
        raise TimeoutError from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def default_readers(home: Path) -> dict[str, Reader]:
    """Build independently kill-bounded readers for qualified local aggregates."""
    timeouts = {spec.adapter_id: spec.timeout_ms for spec in SPECS}
    return {
        adapter_id: Reader(
            adapter_id=adapter_id,
            home=home,
            timeout_ms=timeouts[adapter_id],
        )
        for adapter_id in _IMPLEMENTED
    }


def _worker(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--worker" or argv[1] not in _IMPLEMENTED:
        return 2
    result_fd = os.dup(sys.stdout.fileno())
    devnull = os.open(os.devnull, os.O_WRONLY)
    status = 0
    payload = None
    try:
        os.dup2(devnull, sys.stdout.fileno())
        os.dup2(devnull, sys.stderr.fileno())
        try:
            payload = _read_source(argv[1], Path(argv[2]))
        except PermissionError:
            status = 5
        except ConnectionError:
            status = 6
        except ValueError:
            status = 7
        except Exception:  # noqa: BLE001 - the parent exposes only a fixed failure code
            status = 3
    finally:
        os.close(devnull)
    if status:
        os.close(result_fd)
        return status
    if payload is None:
        os.close(result_fd)
        return 3
    output = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(output.encode()) > 8_192:
        os.close(result_fd)
        return 4
    os.write(result_fd, output.encode())
    os.close(result_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker(sys.argv[1:]))
