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
    OPERATOR_ID,
    ExactPrincipalBackend,
    FileTrustedIssuerBackend,
    SQLiteCapabilityState,
)

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
    approved = Principal(principal_id=OPERATOR_ID, subject=OPERATOR_ID, kind="human")
    backend = ExactPrincipalBackend(approved, "a" * 64)

    assert backend.snapshot(approved).active is True
    with pytest.raises(PermissionError, match="not approved"):
        backend.snapshot(approved.model_copy(update={"principal_id": "other"}))


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
