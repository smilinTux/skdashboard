"""Exact, fail-closed Casey operator policy for SKDashboard.

This module is an authorization taxonomy, not an actuator.  It deliberately
contains no wildcard matching, proxy capability, or route execution support.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Final

CASEY_FINGERPRINT: Final = "AD80D077A047BABF29EEC97AF454FDBC3B1C37D9"
POLICY_RESOURCE: Final = "policies/casey-dashboard-operator.v1.json"
POLICY_SCHEMA: Final = "skdashboard-human-operator-policy/v1"

READ_SCOPES: Final = frozenset(
    {
        "skdashboard.read",
        "skdashboard.events.read",
        "skdashboard.owner-policy.read",
        "skdashboard.audit.read",
    }
)
COMMAND_SCOPES: Final = frozenset(
    {
        "skdashboard.card.command",
        "skdashboard.change.command",
        "skdashboard.cmdb.command",
        "skdashboard.session.revoke",
    }
)
ADMINISTRATOR_SCOPES: Final = READ_SCOPES | COMMAND_SCOPES
DENIED_AUTHORITIES: Final = frozenset(
    {
        "credential",
        "external_action",
        "filesystem",
        "protected_matter",
        "provider",
        "shell",
        "unrestricted_proxy",
        "wildcard",
    }
)

_ALLOWED_PURPOSES: Final = {
    "skdashboard.read": frozenset({"dashboard-read"}),
    "skdashboard.events.read": frozenset({"dashboard-events-read"}),
    "skdashboard.owner-policy.read": frozenset({"owner-policy-inspection"}),
    "skdashboard.audit.read": frozenset({"dashboard-audit-inspection"}),
    "skdashboard.card.command": frozenset({"dashboard-card-administration"}),
    "skdashboard.change.command": frozenset({"dashboard-change-administration"}),
    "skdashboard.cmdb.command": frozenset({"dashboard-cmdb-administration"}),
    "skdashboard.session.revoke": frozenset({"dashboard-session-revocation"}),
}
_ALLOWED_RESOURCES: Final = {
    "skdashboard.read": frozenset({"dashboard:authorized-projection"}),
    "skdashboard.events.read": frozenset({"dashboard:event-stream"}),
    "skdashboard.owner-policy.read": frozenset({"dashboard:owner-policy"}),
    "skdashboard.audit.read": frozenset({"dashboard:audit"}),
    "skdashboard.card.command": frozenset({"dashboard:cards"}),
    "skdashboard.change.command": frozenset({"dashboard:changes"}),
    "skdashboard.cmdb.command": frozenset({"dashboard:cmdb"}),
    "skdashboard.session.revoke": frozenset({"dashboard:sessions"}),
}


@dataclass(frozen=True)
class HumanOperatorPolicy:
    principal_id: str
    fingerprint: str
    oidc_subject: str
    audience: str
    status: str
    expires_at: None
    read_scopes: frozenset[str]
    command_scopes: frozenset[str]
    denied_authorities: frozenset[str]

    @property
    def scopes(self) -> frozenset[str]:
        return self.read_scopes | self.command_scopes

    @property
    def oidc_scopes(self) -> str:
        return " ".join(("openid", *sorted(self.scopes)))

    def resolve_oidc_principal(self, *, subject: str, audience: str) -> str:
        """Map one verified OIDC subject to Casey's one durable principal."""
        if (
            self.status != "active_until_revoked"
            or audience != self.audience
            or subject != self.oidc_subject
        ):
            raise PermissionError("operator principal is not current")
        return self.principal_id

    def authorize(self, *, capability: str, purpose: str, resource: str) -> bool:
        """Return an exact decision. Unknown, wildcard, and cross-surface input denies."""
        return (
            self.status == "active_until_revoked"
            and capability in self.scopes
            and "*" not in (capability, purpose, resource)
            and purpose in _ALLOWED_PURPOSES.get(capability, ())
            and resource in _ALLOWED_RESOURCES.get(capability, ())
        )


def _exact_string_list(value: object) -> frozenset[str]:
    if (
        type(value) is not list
        or not value
        or any(type(item) is not str or not item or "*" in item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("operator policy contains an invalid authority list")
    return frozenset(value)


def load_casey_operator_policy() -> HumanOperatorPolicy:
    """Load the versioned source policy and reject any semantic widening."""
    raw = files("skdashboard").joinpath(POLICY_RESOURCE).read_text(encoding="utf-8")
    value = json.loads(raw)
    required = {
        "schema_version",
        "decision_source",
        "principal_id",
        "fingerprint",
        "oidc_subject",
        "audience",
        "status",
        "expires_at",
        "read_scopes",
        "command_scopes",
        "denied_authorities",
    }
    if type(value) is not dict or set(value) != required:
        raise ValueError("operator policy schema is not exact")
    policy = HumanOperatorPolicy(
        principal_id=value["principal_id"],
        fingerprint=value["fingerprint"],
        oidc_subject=value["oidc_subject"],
        audience=value["audience"],
        status=value["status"],
        expires_at=value["expires_at"],
        read_scopes=_exact_string_list(value["read_scopes"]),
        command_scopes=_exact_string_list(value["command_scopes"]),
        denied_authorities=_exact_string_list(value["denied_authorities"]),
    )
    if (
        value["schema_version"] != POLICY_SCHEMA
        or value["decision_source"] != "card:9fdd672b"
        or policy.principal_id != CASEY_FINGERPRINT
        or policy.fingerprint != CASEY_FINGERPRINT
        or policy.oidc_subject != f"device:{CASEY_FINGERPRINT.lower()}"
        or policy.audience != "skdashboard"
        or policy.status != "active_until_revoked"
        or policy.expires_at is not None
        or policy.read_scopes != READ_SCOPES
        or policy.command_scopes != COMMAND_SCOPES
        or policy.denied_authorities != DENIED_AUTHORITIES
        or policy.read_scopes & policy.command_scopes
    ):
        raise ValueError("operator policy is outside the exact Casey dashboard binding")
    return policy


CASEY_OPERATOR_POLICY: Final = load_casey_operator_policy()

__all__ = [
    "ADMINISTRATOR_SCOPES",
    "CASEY_FINGERPRINT",
    "CASEY_OPERATOR_POLICY",
    "COMMAND_SCOPES",
    "DENIED_AUTHORITIES",
    "HumanOperatorPolicy",
    "READ_SCOPES",
    "load_casey_operator_policy",
]
