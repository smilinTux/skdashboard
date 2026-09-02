import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from capauth import Principal

from skdashboard.runtime_authorizer import (
    CAPABILITIES,
    ExactPrincipalBackend,
    FileTrustedIssuerBackend,
    SQLiteCapabilityState,
)

# Test fingerprints for different operators
CASEY_FINGERPRINT = "AD80D077A047BABF29EEC97AF454FDBC3B1C37D9"
JARVIS_FINGERPRINT = "C8D406A46F2DF4894E4FB41580A638570C9D41C4"

FINGERPRINT = "DCE38ED7BC9D95D724B5FE7FECF9D6A423EC83F5"


class Verifier:
    def verify_bytes(self, payload, signature):
        return payload.startswith(b"{") and signature == "test signature"


def _private(path: Path, value: bytes) -> str:
    path.write_bytes(value)
    path.chmod(0o600)
    return hashlib.sha256(value).hexdigest()


def _policy() -> bytes:
    return json.dumps(
        {
            "audiences": ["skdashboard"],
            "capabilities": sorted(CAPABILITIES),
            "denials": [
                "mutation",
                "external_action",
                "protected_matter",
                "key_export",
                "restart",
                "public_ingress",
            ],
            "failover_identity_ids": [],
            "issuer": {
                "fingerprint": FINGERPRINT,
                "host": "chiap08",
                "service": "skdashboard-read-only",
            },
            "policy_version": "skdashboard-read-only/v1",
            "principal_types": ["human"],
            "schema_version": "skdashboard-scoped-trust-policy/v1",
            "scope_ceiling": "read_only",
            "status": "active_local_only",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def test_file_trust_backend_is_exact_and_reloads_current_bytes(tmp_path: Path) -> None:
    policy = tmp_path / "trusted.json"
    signature = tmp_path / "trusted.json.asc"
    expected = _private(policy, _policy())
    signature_hash = _private(signature, b"test signature")
    backend = FileTrustedIssuerBackend(
        path=policy,
        expected_sha256=expected,
        signature_path=signature,
        expected_signature_sha256=signature_hash,
        fingerprint=FINGERPRINT,
        verifier=Verifier(),
        expected_uid=policy.stat().st_uid,
    )

    snapshot = backend.snapshot()

    assert snapshot.revision == expected
    assert snapshot.issuers[0].fingerprint == FINGERPRINT
    assert snapshot.issuers[0].audiences == frozenset({"skdashboard"})
    assert snapshot.issuers[0].principal_kinds == frozenset({"human"})
    policy.write_bytes(_policy() + b"\n")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        backend.snapshot()


def test_file_trust_backend_rejects_scope_widening(tmp_path: Path) -> None:
    policy = tmp_path / "trusted.json"
    signature = tmp_path / "trusted.json.asc"
    widened = json.loads(_policy())
    widened["denials"].remove("mutation")
    payload = json.dumps(widened, sort_keys=True, separators=(",", ":")).encode("ascii")
    expected = _private(policy, payload)
    signature_hash = _private(signature, b"test signature")
    backend = FileTrustedIssuerBackend(
        path=policy,
        expected_sha256=expected,
        signature_path=signature,
        expected_signature_sha256=signature_hash,
        fingerprint=FINGERPRINT,
        verifier=Verifier(),
        expected_uid=policy.stat().st_uid,
    )

    with pytest.raises(ValueError, match="outside the exact binding"):
        backend.snapshot()


def test_exact_principal_backend_rejects_every_other_operator() -> None:
    # Test with Casey fingerprint
    approved = Principal(principal_id=CASEY_FINGERPRINT, subject=CASEY_FINGERPRINT, kind="human")
    backend = ExactPrincipalBackend(approved, "a" * 64)

    assert backend.snapshot(approved).active is True
    # Reject different principal_id
    with pytest.raises(PermissionError, match="not approved"):
        backend.snapshot(approved.model_copy(update={"principal_id": "other"}))


def test_exact_principal_backend_accepts_casey_fingerprint() -> None:
    """Prove Casey fingerprint AD80 composes when uniquely and exactly selected."""
    approved = Principal(
        principal_id=CASEY_FINGERPRINT,
        subject=CASEY_FINGERPRINT,
        kind="human",
    )
    backend = ExactPrincipalBackend(approved, "a" * 64)
    snapshot = backend.snapshot(approved)

    assert snapshot.active is True
    assert snapshot.principal.principal_id == CASEY_FINGERPRINT
    assert snapshot.principal.subject == CASEY_FINGERPRINT


def test_exact_principal_backend_accepts_jarvis_fingerprint() -> None:
    """Prove Jarvis fingerprint C8D composes when uniquely and exactly selected."""
    approved = Principal(
        principal_id=JARVIS_FINGERPRINT,
        subject=JARVIS_FINGERPRINT,
        kind="human",
    )
    backend = ExactPrincipalBackend(approved, "b" * 64)
    snapshot = backend.snapshot(approved)

    assert snapshot.active is True
    assert snapshot.principal.principal_id == JARVIS_FINGERPRINT
    assert snapshot.principal.subject == JARVIS_FINGERPRINT


def test_exact_principal_backend_rejects_mismatched_subject() -> None:
    """Prove mismatched subject (subject != acting_principal_id) fails closed."""
    mismatched = Principal(
        principal_id=CASEY_FINGERPRINT,
        subject=JARVIS_FINGERPRINT,  # Mismatched subject
        kind="human",
    )
    with pytest.raises(ValueError, match="exact current human principal is required"):
        ExactPrincipalBackend(mismatched, "a" * 64)


def test_exact_principal_backend_requires_subject_equals_principal_id() -> None:
    """Prove subject must equal acting_principal_id (no hardcoded fingerprint)."""
    # Valid: subject equals principal_id
    valid_casey = Principal(
        principal_id=CASEY_FINGERPRINT,
        subject=CASEY_FINGERPRINT,
        kind="human",
    )
    backend_casey = ExactPrincipalBackend(valid_casey, "a" * 64)
    assert backend_casey.snapshot(valid_casey).active is True

    # Valid: subject equals principal_id (different fingerprint)
    valid_jarvis = Principal(
        principal_id=JARVIS_FINGERPRINT,
        subject=JARVIS_FINGERPRINT,
        kind="human",
    )
    backend_jarvis = ExactPrincipalBackend(valid_jarvis, "b" * 64)
    assert backend_jarvis.snapshot(valid_jarvis).active is True

    # Invalid: subject does not equal principal_id
    invalid = Principal(
        principal_id=CASEY_FINGERPRINT,
        subject=CASEY_FINGERPRINT.lower(),  # Different case
        kind="human",
    )
    with pytest.raises(ValueError, match="exact current human principal is required"):
        ExactPrincipalBackend(invalid, "a" * 64)


def test_exact_principal_backend_requires_valid_revision() -> None:
    """Prove invalid revision format fails closed."""
    principal = Principal(
        principal_id=CASEY_FINGERPRINT,
        subject=CASEY_FINGERPRINT,
        kind="human",
    )

    # Invalid: too short
    with pytest.raises(ValueError, match="exact current human principal is required"):
        ExactPrincipalBackend(principal, "a" * 63)

    # Invalid: too long
    with pytest.raises(ValueError, match="exact current human principal is required"):
        ExactPrincipalBackend(principal, "a" * 65)

    # Invalid: non-hex characters
    with pytest.raises(ValueError, match="exact current human principal is required"):
        ExactPrincipalBackend(principal, "g" * 64)

    # Valid: exactly 64 hex characters
    backend = ExactPrincipalBackend(principal, "a" * 64)
    assert backend.snapshot(principal).active is True


def test_exact_principal_backend_requires_human_kind() -> None:
    """Prove non-human principal kind fails closed."""
    machine = Principal(
        principal_id=CASEY_FINGERPRINT,
        subject=CASEY_FINGERPRINT,
        kind="machine",
    )
    with pytest.raises(ValueError, match="exact current human principal is required"):
        ExactPrincipalBackend(machine, "a" * 64)


def test_sqlite_state_is_durable_replay_safe_and_audit_append_only(tmp_path: Path) -> None:
    database = tmp_path / "private" / "capability-state.db"
    state = SQLiteCapabilityState(database)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    digest = "b" * 64

    assert state.reserve(credential_digest=digest, decision_id="one", expires_at=expiry) is True
    assert state.reserve(credential_digest=digest, decision_id="two", expires_at=expiry) is False
    state.record(SimpleNamespace(model_dump_json=lambda: '{"allow":true}'))

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            connection.execute("UPDATE audit SET decision = '{}' WHERE sequence = 1")
        connection.execute(
            "INSERT INTO revocations VALUES (?, ?)",
            (digest, datetime.now(timezone.utc).isoformat()),
        )
    assert state.snapshot((digest,)).revoked_credential_digests == frozenset({digest})
    assert database.stat().st_mode & 0o777 == 0o600
