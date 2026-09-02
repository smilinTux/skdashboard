"""Governed Economy provider for the authenticated read-only control plane.

Bridges the existing bounded readers (SKCounter via ``dashboard_skcounter``,
cost state, and SKJoule via ``JouleEngine.get_network_stats``) into the
authenticated read-only runtime. The provider is read-only: it composes
already-bounded projections and fails closed on missing or stale sources,
never rendering them as zero or healthy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "economy-projection/v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _skcounter_item(
    lane: str,
    *,
    now: datetime | None = None,
    home: Path | None = None,
    usage: dict | None = None,
    filters: dict[str, str] | None = None,
) -> dict:
    """One SKCounter lane item: tokens, bounded cost with explicit state."""
    from .dashboard_skcounter import TOKEN_FIELDS

    now = now or datetime.now(timezone.utc)
    if usage is None:
        from .dashboard_skcounter import get_ai_usage

        if home is None:
            raise ValueError("home is required to read SKCounter usage")
        usage = get_ai_usage(
            home,
            {"lane": lane, **(filters or {})},
        )
    summary = usage.get("summary") or {}
    if not isinstance(summary, dict):
        raise ValueError("SKCounter summary is malformed")
    cost_state = summary.get("cost_state", "unavailable")
    cost = summary.get("cost_usd") if cost_state == "available" else None
    item = {
        "measurement_lane": usage.get("selected_lane", lane),
        "tokens": {key: summary.get(key, 0) for key in TOKEN_FIELDS},
        "cost_usd": cost,
        "cost_state": cost_state,
        "observed_at": usage.get("generated_at"),
    }
    from .dashboard_skcounter import DELAYED_SECONDS

    observed = item["observed_at"]
    if observed:
        try:
            obs_dt = datetime.fromisoformat(observed.replace("Z", "+00:00")).astimezone(timezone.utc)
            age = max(0, int((now - obs_dt).total_seconds()))
            item["truth_state"] = "stale" if age > DELAYED_SECONDS else "current"
        except ValueError:
            item["truth_state"] = "unavailable"
    elif usage.get("status") in {"degraded", "empty"}:
        item["truth_state"] = "unavailable"
    else:
        item["truth_state"] = "current"
    return item


def _joule_item(home: Path) -> dict:
    """One SKJoule item: total supply and active agent count, fail closed."""
    from skcapstone.skjoule import JouleEngine

    stats = JouleEngine(home=home).get_network_stats()
    balances = stats.agent_balances
    if not isinstance(balances, dict) or not isinstance(stats.active_agents, int):
        raise ValueError("Joule network stats malformed")
    total_supply = sum(balances.values())
    has_observations = bool(balances)
    item = {
        "source": "skjoule.wallet",
        "total_supply": total_supply,
        "active_agents": stats.active_agents,
        "has_observations": has_observations,
        "observed_at": _utc_now(),
    }
    item["truth_state"] = "current" if has_observations else "unavailable"
    return item


def _freshness(truth_state: str, observed_at: str | None, projected_at: str, age_seconds: int) -> dict:
    return {
        "truth_state": truth_state,
        "observed_at": observed_at,
        "projected_at": projected_at,
        "age_seconds": age_seconds,
    }


def get_economy_projection(
    home: Path,
    context: dict | None = None,
    *,
    now: datetime | None = None,
    lane: str = "harness_reported",
    filters: dict[str, str] | None = None,
) -> dict:
    """Project the governed Economy workspace into a bounded read model.

    Args:
        home: Agent home directory, the same root other dashboard panels query.
        context: Control-plane context (role, scope, window, baseline, service).
        now: Injection point for deterministic tests.

    Returns:
        dict with explicit per-source items, a freshness block, and a
        fail-closed truth_state. Missing or stale sources surface as
        "unavailable" / "stale" states, never as zero-valued "current".
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    context = context or {}
    if context:
        for key in ("role", "scope", "window", "baseline", "service"):
            if key in context and not isinstance(context[key], str):
                raise ValueError(f"context.{key} must be a string")

    errors: list[str] = []
    items: list[dict] = []

    for requested_lane in ("harness_reported", "gateway_observed"):
        try:
            item = _skcounter_item(requested_lane, home=home, now=now, filters=filters)
            items.append(item)
        except Exception as exc:  # noqa: BLE001 - fail closed per source
            errors.append(f"skcounter.{requested_lane} unavailable: {exc}")
            items.append(
                {
                    "measurement_lane": lane,
                    "tokens": None,
                    "cost_usd": None,
                    "cost_state": "unavailable",
                    "observed_at": None,
                    "truth_state": "unavailable",
                }
            )

    try:
        items.append(_joule_item(home))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"skjoule.wallet unavailable: {exc}")
        items.append(
            {
                "source": "skjoule.wallet",
                "total_supply": None,
                "active_agents": None,
                "has_observations": False,
                "observed_at": None,
                "truth_state": "unavailable",
            }
        )

    projected_at = now.isoformat().replace("+00:00", "Z")
    observed_values = [item.get("observed_at") for item in items if item.get("observed_at")]
    observed_at = min(observed_values) if observed_values else None
    age_seconds = 0
    if observed_at:
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            age_seconds = max(0, int((now - observed).total_seconds()))
        except ValueError:
            observed_at = None

    # Fail closed: any per-source error or missing observed_at makes the
    # projection not fully current. Stale data (age beyond the SKCounter
    # fresh/delayed thresholds) is surfaced as "stale", never as zero.
    # Derive a single truth_state from the per-item states. The SKCounter
    # items carry an explicit "truth_state" when the source is missing or
    # stale; SKJoule carries "has_observations". This keeps missing or
    # stale sources from rendering as zero-valued "current".
    item_states = set()
    for item in items:
        if item.get("truth_state"):
            item_states.add(item["truth_state"])
    if errors:
        # Fail closed on any per-source error. When EVERY source failed
        # (all item states are unavailable) the projection itself is
        # "unavailable"; otherwise at least one source still reported, so
        # it is "partial".
        if item_states == {"unavailable"}:
            truth_state = "unavailable"
        else:
            truth_state = "partial"
    elif "unavailable" in item_states:
        truth_state = "unavailable"
    elif "stale" in item_states:
        truth_state = "stale"
    elif item_states:
        truth_state = "current"
    elif observed_at is None:
        truth_state = "unavailable"
    else:
        from .dashboard_skcounter import DELAYED_SECONDS, FRESH_SECONDS

        if age_seconds > DELAYED_SECONDS:
            truth_state = "stale"
        elif age_seconds > FRESH_SECONDS:
            truth_state = "partial"
        else:
            truth_state = "current"

    return {
        "schema_version": SCHEMA_VERSION,
        "projection_id": f"economy-{now.isoformat().replace(':', '').replace('+00:00', '')}",
        "scope": {
            "role": context.get("role", "operator"),
            "scope": context.get("scope", "estate"),
            "window": context.get("window", "latest"),
            "baseline": context.get("baseline", "none"),
            "service": context.get("service", "all"),
        },
        "items": items,
        "freshness": _freshness(truth_state, observed_at, projected_at, age_seconds),
        "errors": errors,
        "provenance": {
            "sources": ["skcounter.harness", "skcounter.gateway_observed", "skjoule.wallet"],
            "readers": ["dashboard_skcounter.get_ai_usage", "JouleEngine.get_network_stats"],
            "mode": "read-only, bounded aggregates only",
        },
        "generated_at": projected_at,
    }


class EconomyProjectionProvider:
    """Read Economy evidence only while the exact CapAuth decision remains current."""

    def read(self, context, query, home, *, currentness_verifier=None):
        if currentness_verifier is not None:
            if currentness_verifier.check_before_owner_read(context).value != "allow":
                raise PermissionError("control-plane decision is not current")
        scope = {
            "role": getattr(query, "role", "operator"),
            "scope": getattr(query, "scope", "estate"),
            "window": getattr(query, "window", "latest"),
            "baseline": getattr(query, "baseline", "none"),
            "service": getattr(query, "service", "all"),
        }
        filters = {
            key: value
            for key, value in {
                "from": getattr(query, "from", ""),
                "to": getattr(query, "to", ""),
            }.items()
            if value
        }
        projection = get_economy_projection(
            home,
            scope,
            lane=getattr(query, "measurement_lane", "harness_reported"),
            filters=filters,
        )
        if currentness_verifier is not None:
            if currentness_verifier.check_after_owner_read(context).value != "allow":
                raise PermissionError("control-plane decision expired during owner read")
        return projection


__all__ = [
    "EconomyProjectionProvider",
    "SCHEMA_VERSION",
    "get_economy_projection",
]
