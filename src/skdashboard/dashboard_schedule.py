"""Fail-closed canonical Schedule projection provider.

The provider deliberately consumes an already authorized, tenant-bounded owner
snapshot. It never parses card titles or descriptions and it never guesses a
schedule value. Owner policy selection and filtering therefore happen before
this module sees an owner record.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

SCHEMA_VERSION = "1.0.0"
PROJECTION_VERSION = "schedule-projection/1.0.0"
AUTHORIZATION_TARGET = "/api/v1/schedule/projection"
AUTHORIZATION_CAPABILITY = "skdashboard.read"
MAX_ITEMS = 10_000
MAX_DEPENDENCIES = 20_000
MAX_OVERLAYS = 5_000

DATE_FIELDS = (
    "baseline_start",
    "baseline_target",
    "planned_start",
    "planned_target",
    "actual_start",
    "actual_finish",
    "forecast_start",
    "forecast_target",
)
DATE_STATES = frozenset(
    {"known", "unknown", "stale", "partial", "unavailable", "policy_filtered", "not_applicable"}
)
TRUTH_STATES = frozenset(
    {"current", "stale", "partial", "unavailable", "unreachable", "unknown", "not_applicable"}
)
ITEM_TYPES = frozenset(
    {
        "outcome",
        "project",
        "epic",
        "release",
        "milestone",
        "work_package",
        "team",
        "service",
        "architecture_migration",
        "itil_change_window",
    }
)
ROLE_MAP = MappingProxyType(
    {
        "operator": "portfolio",
        "portfolio": "portfolio",
        "project-manager": "project_manager",
        "project_manager": "project_manager",
        "architect": "architect",
        "service": "service",
        "team": "team",
    }
)

# This table is the pinned source contract. A producer may use any storage
# implementation, but these are the only authorities from which fields may be
# copied. In particular, title and description text are never a schedule source.
FIELD_PROVENANCE = MappingProxyType(
    {
        "item_id": "canonical coordination record.record_id",
        "title": "canonical coordination record.display_title",
        "item_type": "canonical coordination record.semantic_type",
        "owner_service_id": "canonical coordination record.owner_service_id",
        "service_id": "canonical coordination record.service_id",
        "status": "canonical coordination record.lifecycle_status",
        "truth_state": "source record.truth_state plus source currentness",
        "visibility": "owner-policy decision for the exact record and field",
        "dates.baseline_start": "immutable schedule baseline.start",
        "dates.baseline_target": "immutable schedule baseline.target",
        "dates.planned_start": "owner schedule plan.start",
        "dates.planned_target": "owner schedule plan.target",
        "dates.actual_start": "owner execution actual.start",
        "dates.actual_finish": "owner execution actual.finish",
        "dates.forecast_start": "authorized forecast artifact.range_start",
        "dates.forecast_target": "authorized forecast artifact.range_target",
        "baseline_variance": "planned_target minus baseline_target when both are known",
        "progress": "canonical coordination record.explicit_progress only",
        "dependencies": "canonical coordination dependency records only",
        "release": "canonical release record semantic_type=release",
        "milestone": "canonical milestone record semantic_type=milestone",
        "overlays.itil_change_window": "canonical ITIL change.window",
        "overlays.blackout": "canonical ITIL blackout.window",
        "overlays.architecture_migration": "canonical architecture migration.window",
        "overlays.architecture_deprecation": "canonical architecture deprecation.window",
    }
)


class CanonicalScheduleSource(Protocol):
    """Atomic policy-filtered source used by :class:`ScheduleProjectionProvider`.

    ``read`` must select policy by the supplied authorization target before
    enumerating owner records and return one snapshot revision. Returning
    ``None`` means unavailable. It must not return records from another tenant.
    """

    def read(self, context, request: "ScheduleSourceRequest", home: Path) -> Mapping | None: ...


@dataclass(frozen=True)
class ScheduleSourceRequest:
    tenant_id: str
    role: str
    scope: str
    service_id: str
    authorization_target: str = AUTHORIZATION_TARGET
    schema_version: str = SCHEMA_VERSION


class ScheduleProjectionProvider:
    """Map one authorized canonical snapshot into every Schedule lens.

    Roadmap, Gantt, and Flow are presentation choices and are intentionally not
    included in the source request, projection scope, ID, version, or hash.
    Consequently all three receive byte-identical data for an otherwise equal
    request. Any malformed, cross-tenant, stale-authority, or unavailable read
    raises a non-descriptive error for the API boundary to turn into a constant
    503 response.
    """

    def __init__(
        self,
        source: CanonicalScheduleSource,
        *,
        tenant_id: str,
        clock=lambda: datetime.now(timezone.utc),
        max_source_age_seconds: int = 300,
    ) -> None:
        if not _identifier(tenant_id) or not 1 <= max_source_age_seconds <= 86_400:
            raise ValueError("invalid Schedule provider configuration")
        self._source = source
        self._tenant_id = tenant_id
        self._clock = clock
        self._max_source_age_seconds = max_source_age_seconds

    def read(self, context, query, home, *, currentness_verifier):
        try:
            request = self._request(context, query)
            if currentness_verifier.check_before_owner_read(context).value != "allow":
                raise PermissionError
            snapshot = self._source.read(context, request, Path(home))
            if currentness_verifier.check_after_owner_read(context).value != "allow":
                raise PermissionError
            projection = self._project(snapshot, request, query)
            # A final check closes a policy change during validation/serialization.
            if currentness_verifier.check_after_owner_read(context).value != "allow":
                raise PermissionError
            return projection
        except Exception as exc:
            raise PermissionError("authorized schedule projection unavailable") from exc

    def _request(self, context, query) -> ScheduleSourceRequest:
        binding = context.binding
        if (
            binding.target != AUTHORIZATION_TARGET
            or binding.capability != AUTHORIZATION_CAPABILITY
            or not context.joined_decision.allow
        ):
            raise PermissionError
        role = ROLE_MAP.get(query.get("role"))
        if role is None or query.get("scope") != "estate" or query.get("service") != "all":
            raise PermissionError
        return ScheduleSourceRequest(
            tenant_id=self._tenant_id,
            role=role,
            scope="estate",
            service_id="all",
        )

    def _project(self, raw, request: ScheduleSourceRequest, query) -> dict:
        if not isinstance(raw, Mapping):
            raise ValueError
        snapshot = deepcopy(dict(raw))
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
        if set(snapshot) != required or snapshot["schema_version"] != SCHEMA_VERSION:
            raise ValueError
        authorization = snapshot["authorization"]
        if (
            snapshot["tenant_id"] != request.tenant_id
            or not isinstance(authorization, Mapping)
            or authorization.get("state") != "authorized"
            or authorization.get("target") != AUTHORIZATION_TARGET
            or authorization.get("tenant_id") != request.tenant_id
            or authorization.get("role") != request.role
            or authorization.get("scope") != request.scope
            or not _identifier(authorization.get("policy_decision_ref"))
            or not _identifier(authorization.get("owner_policy_revision"))
        ):
            raise PermissionError
        observed = _instant(snapshot["observed_at"])
        projected = _instant(snapshot["projected_at"])
        now = self._now()
        if observed is None or projected is None or projected < observed:
            raise ValueError
        age = (now - observed).total_seconds()
        if age < -5 or age > self._max_source_age_seconds:
            raise PermissionError

        items = snapshot["items"]
        dependencies = snapshot["dependencies"]
        overlays = snapshot["overlays"]
        if (
            not isinstance(items, list)
            or len(items) > MAX_ITEMS
            or not isinstance(dependencies, list)
            or len(dependencies) > MAX_DEPENDENCIES
            or not isinstance(overlays, list)
            or len(overlays) > MAX_OVERLAYS
        ):
            raise ValueError
        projected_items = [self._item(value, request) for value in items]
        ids = [value["item_id"] for value in projected_items]
        if len(ids) != len(set(ids)) or ids != sorted(ids):
            raise ValueError
        projected_dependencies = [self._dependency(value, request, set(ids)) for value in dependencies]
        projected_overlays = [self._overlay(value, request) for value in overlays]
        cycles = _cycles(projected_dependencies, set(ids))
        reasons = _critical_path_reasons(projected_items, projected_dependencies, projected_overlays, cycles)

        scope = {"role": request.role, "tenant_id": request.tenant_id}
        projection = {
            "schema_version": SCHEMA_VERSION,
            "projection_id": f"schedule:{request.tenant_id}:{request.role}:estate",
            "projection_version": str(snapshot["snapshot_revision"]),
            "projection_hash": "",
            "scope": scope,
            "display_timezone": query["timezone"],
            "observed_at": _text(observed),
            "projected_at": _text(projected),
            "truth_state": _projection_truth(projected_items),
            "visibility": {
                "state": "visible",
                "authorization": "authorized",
                "policy_decision_ref": authorization["policy_decision_ref"],
            },
            "source_watermarks": _watermarks(snapshot["source_watermarks"]),
            "field_provenance": dict(FIELD_PROVENANCE),
            "items": projected_items,
            "dependencies": projected_dependencies,
            "overlays": projected_overlays,
            "cycle_analysis": {
                "state": "cycles_detected" if cycles else "acyclic",
                "cycle_item_ids": sorted(cycles),
                "evidence_refs": [f"schedule://dependency-cycle/{item_id}" for item_id in sorted(cycles)],
            },
            "critical_path": {
                "state": "unavailable" if reasons else "not_applicable",
                "item_ids": [],
                "reasons": reasons or ["not_applicable"],
            },
            "individual_ranking_prohibited": True,
            "errors": [],
        }
        projection["projection_hash"] = _hash({**projection, "projection_hash": None})
        return projection

    def _item(self, value, request: ScheduleSourceRequest) -> dict:
        if not isinstance(value, Mapping) or value.get("tenant_id") != request.tenant_id:
            raise PermissionError
        required = {
            "tenant_id", "record_id", "display_title", "semantic_type", "owner_service_id",
            "service_id", "lifecycle_status", "truth_state", "visibility", "dates",
            "explicit_progress", "source_watermarks", "evidence_refs",
        }
        if set(value) != required or value["semantic_type"] not in ITEM_TYPES:
            raise ValueError
        if value["truth_state"] not in TRUTH_STATES:
            raise ValueError
        visibility = _visibility(value["visibility"])
        dates = value["dates"]
        if not isinstance(dates, Mapping) or set(dates) != set(DATE_FIELDS):
            raise ValueError
        projected_dates = {field: _date(dates[field]) for field in DATE_FIELDS}
        progress = value["explicit_progress"]
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= progress <= 1):
            raise ValueError
        variance = _variance(projected_dates["baseline_target"], projected_dates["planned_target"])
        item_id = _bounded(value["record_id"], 128)
        title = _bounded(value["display_title"], 500)
        owner = _bounded(value["owner_service_id"], 128)
        service = _bounded(value["service_id"], 128)
        status = _bounded(value["lifecycle_status"], 64)
        if not all((item_id, title, owner, service, status)):
            raise ValueError
        return {
            "item_id": item_id,
            "title": title,
            "item_type": value["semantic_type"],
            "owner_service_id": owner,
            "service_id": service,
            "status": status,
            "truth_state": value["truth_state"],
            "visibility": visibility,
            "dates": projected_dates,
            "baseline_variance": variance,
            "progress": progress,
            "progress_basis": "canonical explicit_progress; no value inferred" if progress is None else "canonical explicit_progress",
            "rollup": {
                "state": "not_applicable",
                "eligible_children": 0,
                "included_children": 0,
                "start": _date({"state": "not_applicable", "instant": None, "reason": "no canonical rollup supplied"}),
                "end": _date({"state": "not_applicable", "instant": None, "reason": "no canonical rollup supplied"}),
                "progress": None,
                "progress_basis": "no canonical rollup supplied",
                "exclusions": [],
            },
            "source_watermarks": _watermarks(value["source_watermarks"]),
            "evidence_refs": _refs(value["evidence_refs"]),
        }

    def _dependency(self, value, request: ScheduleSourceRequest, ids: set[str]) -> dict:
        if not isinstance(value, Mapping) or value.get("tenant_id") != request.tenant_id:
            raise PermissionError
        required = {
            "tenant_id", "dependency_id", "source_item_id", "target_item_id", "edge_type",
            "direction", "lag_seconds", "truth_state", "visibility", "blocker_state", "evidence_refs",
        }
        if set(value) != required:
            raise ValueError
        source = _bounded(value["source_item_id"], 128)
        target = value["target_item_id"]
        target = _bounded(target, 128) if target is not None else None
        direction = value["direction"]
        if source not in ids or (target is not None and target not in ids):
            # A hidden required node must be represented as target=None, never by ID.
            raise PermissionError
        if direction not in {"known", "unknown"} or (direction == "known" and target is None):
            raise ValueError
        edge = {
            "dependency_id": _bounded(value["dependency_id"], 128),
            "source_item_id": source,
            "target_item_id": target,
            "edge_type": value["edge_type"],
            "direction": direction,
            "lag_seconds": value["lag_seconds"],
            "truth_state": value["truth_state"],
            "visibility": _visibility(value["visibility"]),
            "blocker_state": value["blocker_state"],
            "cycle_state": "unknown",
            "evidence_refs": _refs(value["evidence_refs"], minimum=1),
        }
        if None in (edge["dependency_id"], source) or edge["edge_type"] not in {"finish_to_start", "start_to_start", "finish_to_finish", "start_to_finish", "unknown"} or edge["truth_state"] not in TRUTH_STATES or edge["blocker_state"] not in {"blocking", "not_blocking", "unknown"} or (edge["lag_seconds"] is not None and not isinstance(edge["lag_seconds"], int)):
            raise ValueError
        return edge

    def _overlay(self, value, request: ScheduleSourceRequest) -> dict:
        if not isinstance(value, Mapping) or value.get("tenant_id") != request.tenant_id:
            raise PermissionError
        required = {"tenant_id", "overlay_id", "overlay_type", "owner_service_id", "start", "end", "truth_state", "visibility", "conflict_state", "evidence_refs"}
        if set(value) != required:
            raise ValueError
        result = {key: value[key] for key in ("overlay_id", "overlay_type", "owner_service_id", "truth_state", "conflict_state")}
        if not all(_bounded(result[key], 128) for key in ("overlay_id", "owner_service_id")) or result["overlay_type"] not in {"itil_change_window", "blackout", "architecture_migration", "architecture_deprecation"} or result["truth_state"] not in TRUTH_STATES or result["conflict_state"] not in {"clear", "conflict", "unknown"}:
            raise ValueError
        return {**result, "start": _date(value["start"]), "end": _date(value["end"]), "visibility": _visibility(value["visibility"]), "evidence_refs": _refs(value["evidence_refs"])}

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError
        return value.astimezone(timezone.utc)


def _date(value) -> dict:
    if not isinstance(value, Mapping) or set(value) - {"state", "instant", "reason"} or set(value) < {"state", "instant"}:
        raise ValueError
    state = value["state"]
    if state not in DATE_STATES:
        raise ValueError
    instant = _instant(value["instant"]) if value["instant"] is not None else None
    if state == "known":
        if instant is None:
            raise ValueError
        return {"state": state, "instant": _text(instant)}
    if instant is not None or not _bounded(value.get("reason"), 500):
        raise ValueError
    return {"state": state, "instant": None, "reason": value["reason"]}


def _variance(baseline: dict, planned: dict) -> dict:
    if baseline["state"] == planned["state"] == "known":
        seconds = int((_instant(planned["instant"]) - _instant(baseline["instant"])).total_seconds())
        return {"state": "known", "seconds": seconds}
    states = {baseline["state"], planned["state"]}
    if "not_applicable" in states:
        state = "not_applicable"
    elif "unavailable" in states or "policy_filtered" in states:
        state = "unavailable"
    else:
        state = "unknown"
    return {"state": state, "seconds": None, "reason": "canonical baseline or planned target is not known"}


def _visibility(value) -> dict:
    if not isinstance(value, Mapping) or value.get("state") != "visible" or value.get("authorization") != "authorized" or set(value) - {"state", "authorization", "policy_decision_ref"}:
        raise PermissionError
    result = {"state": "visible", "authorization": "authorized"}
    if "policy_decision_ref" in value:
        if not _bounded(value["policy_decision_ref"], 256):
            raise ValueError
        result["policy_decision_ref"] = value["policy_decision_ref"]
    return result


def _watermarks(values) -> list[dict]:
    if not isinstance(values, list) or not values or len(values) > 64:
        raise ValueError
    result = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {"source", "value"} or not _bounded(value["source"], 128) or not _bounded(value["value"], 256):
            raise ValueError
        result.append(dict(value))
    return result


def _refs(values, *, minimum=0) -> list[str]:
    if not isinstance(values, list) or not minimum <= len(values) <= 128 or any(not _bounded(value, 512) for value in values):
        raise ValueError
    return list(values)


def _cycles(edges: list[dict], ids: set[str]) -> set[str]:
    graph = {item_id: [] for item_id in ids}
    for edge in edges:
        if edge["direction"] == "known" and edge["target_item_id"] is not None:
            graph[edge["source_item_id"]].append(edge["target_item_id"])
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            cyclic.update(path[path.index(node) :])
            return
        if node in visited:
            return
        visiting.add(node)
        path.append(node)
        for target in graph[node]:
            visit(target, path)
        path.pop()
        visiting.remove(node)
        visited.add(node)

    for item_id in sorted(ids):
        visit(item_id, [])
    for edge in edges:
        edge["cycle_state"] = "cycle_member" if edge["source_item_id"] in cyclic and edge["target_item_id"] in cyclic else ("unknown" if edge["direction"] == "unknown" else "acyclic")
    return cyclic


def _critical_path_reasons(items, edges, overlays, cycles) -> list[str]:
    reasons = []
    if cycles:
        reasons.append("dependency_cycle")
    if any(edge["direction"] == "unknown" for edge in edges):
        reasons.append("unknown_edge_direction")
    if any(edge["target_item_id"] is None for edge in edges):
        reasons.append("inaccessible_required_node")
    if any(item["dates"][field]["state"] != "known" for item in items for field in ("planned_start", "planned_target")):
        reasons.append("missing_required_dates")
    if any(overlay["overlay_type"] == "blackout" and overlay["conflict_state"] == "conflict" for overlay in overlays):
        reasons.append("conflicting_blackout")
    return reasons


def _projection_truth(items) -> str:
    states = {item["truth_state"] for item in items}
    if not items:
        return "unknown"
    if states == {"current"}:
        return "current"
    if "unavailable" in states:
        return "unavailable"
    if "stale" in states:
        return "stale"
    return "partial"


def _instant(value) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded(value, maximum: int) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value else None


def _identifier(value) -> bool:
    return bool(_bounded(value, 256)) and all(char.isalnum() or char in "._:@/-" for char in value)


def _hash(value) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


__all__ = [
    "AUTHORIZATION_CAPABILITY",
    "AUTHORIZATION_TARGET",
    "CanonicalScheduleSource",
    "FIELD_PROVENANCE",
    "PROJECTION_VERSION",
    "SCHEMA_VERSION",
    "ScheduleProjectionProvider",
    "ScheduleSourceRequest",
]
