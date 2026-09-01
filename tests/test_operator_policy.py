import json
from importlib.resources import files

import pytest

from skdashboard.operator_policy import (
    ADMINISTRATOR_SCOPES,
    CASEY_FINGERPRINT,
    CASEY_OPERATOR_POLICY,
    COMMAND_SCOPES,
    DENIED_AUTHORITIES,
    READ_SCOPES,
    load_casey_operator_policy,
)


def test_casey_maps_to_one_durable_verified_human_operator() -> None:
    policy = load_casey_operator_policy()

    assert policy.fingerprint == CASEY_FINGERPRINT
    assert policy.principal_id == CASEY_FINGERPRINT
    assert policy.status == "active_until_revoked"
    assert policy.expires_at is None
    assert (
        policy.resolve_oidc_principal(
            subject="device:ad80d077a047babf29eec97af454fdbc3b1c37d9",
            audience="skdashboard",
        )
        == CASEY_FINGERPRINT
    )
    for subject, audience in (
        ("device:someone-else", "skdashboard"),
        (policy.oidc_subject, "skgateway"),
        (CASEY_FINGERPRINT, "skdashboard"),
    ):
        with pytest.raises(PermissionError, match="not current"):
            policy.resolve_oidc_principal(subject=subject, audience=audience)


def test_dashboard_administrator_taxonomy_is_explicit_and_separated() -> None:
    assert READ_SCOPES == {
        "skdashboard.audit.read",
        "skdashboard.events.read",
        "skdashboard.owner-policy.read",
        "skdashboard.read",
    }
    assert COMMAND_SCOPES == {
        "skdashboard.card.command",
        "skdashboard.change.command",
        "skdashboard.cmdb.command",
        "skdashboard.session.revoke",
    }
    assert ADMINISTRATOR_SCOPES == READ_SCOPES | COMMAND_SCOPES
    assert not READ_SCOPES & COMMAND_SCOPES
    assert DENIED_AUTHORITIES == {
        "credential",
        "external_action",
        "filesystem",
        "protected_matter",
        "provider",
        "shell",
        "unrestricted_proxy",
        "wildcard",
    }
    assert "*" not in CASEY_OPERATOR_POLICY.oidc_scopes
    assert "skgateway.admin" not in ADMINISTRATOR_SCOPES


@pytest.mark.parametrize(
    ("capability", "purpose", "resource"),
    [
        ("*", "dashboard-read", "dashboard:authorized-projection"),
        ("skdashboard.*", "dashboard-read", "dashboard:authorized-projection"),
        ("skdashboard.read", "*", "dashboard:authorized-projection"),
        ("skdashboard.read", "dashboard-read", "*"),
        ("skdashboard.read", "shell", "dashboard:authorized-projection"),
        ("skdashboard.read", "dashboard-read", "filesystem:/etc"),
        ("skgateway.admin", "provider", "provider:all"),
        ("skdashboard.proxy", "unrestricted_proxy", "https://external.example"),
        ("skdashboard.protected-matter.read", "dashboard-read", "matter:protected"),
        ("skdashboard.credentials.read", "dashboard-read", "credential:all"),
        ("skdashboard.external.execute", "external_action", "external:any"),
        ("agentrun.queue", "dashboard-card-administration", "dashboard:cards"),
    ],
)
def test_unknown_wildcard_and_cross_surface_authority_fail_closed(
    capability: str, purpose: str, resource: str
) -> None:
    assert (
        CASEY_OPERATOR_POLICY.authorize(
            capability=capability,
            purpose=purpose,
            resource=resource,
        )
        is False
    )


def test_exact_purpose_and_resource_bindings_allow_only_named_dashboard_actions() -> None:
    exact = {
        "skdashboard.read": ("dashboard-read", "dashboard:authorized-projection"),
        "skdashboard.events.read": ("dashboard-events-read", "dashboard:event-stream"),
        "skdashboard.owner-policy.read": (
            "owner-policy-inspection",
            "dashboard:owner-policy",
        ),
        "skdashboard.audit.read": ("dashboard-audit-inspection", "dashboard:audit"),
        "skdashboard.card.command": (
            "dashboard-card-administration",
            "dashboard:cards",
        ),
        "skdashboard.change.command": (
            "dashboard-change-administration",
            "dashboard:changes",
        ),
        "skdashboard.cmdb.command": (
            "dashboard-cmdb-administration",
            "dashboard:cmdb",
        ),
        "skdashboard.session.revoke": (
            "dashboard-session-revocation",
            "dashboard:sessions",
        ),
    }
    for capability, (purpose, resource) in exact.items():
        assert CASEY_OPERATOR_POLICY.authorize(
            capability=capability,
            purpose=purpose,
            resource=resource,
        )
        assert not CASEY_OPERATOR_POLICY.authorize(
            capability=capability,
            purpose=purpose + "-other",
            resource=resource,
        )
        assert not CASEY_OPERATOR_POLICY.authorize(
            capability=capability,
            purpose=purpose,
            resource=resource + ":other",
        )


def test_source_policy_records_decision_provenance_without_credentials() -> None:
    value = json.loads(
        files("skdashboard")
        .joinpath("policies/casey-dashboard-operator.v1.json")
        .read_text(encoding="utf-8")
    )
    assert value["decision_source"] == "card:9fdd672b"
    assert set(value) == {
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
    serialized = json.dumps(value).lower()
    for forbidden in ("private_key", "passphrase", "client_secret", "access_token"):
        assert forbidden not in serialized
