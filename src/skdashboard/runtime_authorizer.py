"""Durable fail-closed capability authorization for the read-only runtime."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from capauth import VERIFIER_POLICY_VERSION, CapabilityAuthorizer, IssuerGrant, Principal
from capauth.delegated import (
    AuthorizationDecision,
    PrincipalPolicySnapshot,
    RevocationSnapshot,
    SignedToken,
    TrustedIssuerSnapshot,
)

AUDIENCE = "skdashboard"
CAPABILITIES = frozenset({"skdashboard.read", "skdashboard.events.read"})
ISSUER_FINGERPRINT = "DCE38ED7BC9D95D724B5FE7FECF9D6A423EC83F5"
OPERATOR_ID = "C8D406A46F2DF4894E4FB41580A638570C9D41C4"
PRINCIPAL_KIND = "human"
POLICY_SCHEMA = "skdashboard-scoped-trust-policy/v1"
POLICY_VERSION = "skdashboard-read-only/v1"
POLICY_DENIALS = frozenset(
    {
        "external_action",
        "key_export",
        "mutation",
        "protected_matter",
        "public_ingress",
        "restart",
    }
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_private_file(path: Path, expected_sha256: str, expected_uid: int) -> bytes:
    if (
        not path.is_absolute()
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("an absolute path and exact SHA256 are required")
    metadata = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise PermissionError("runtime policy file custody is invalid")
    value = path.read_bytes()
    if not value or _sha256(value) != expected_sha256:
        raise ValueError("runtime policy file SHA256 mismatch")
    return value


class DedicatedKeyringVerifier:
    """Verify detached signatures using only one exact host-local keyring."""

    __slots__ = ("_executable", "_fingerprint", "_home", "_timeout")

    def __init__(
        self,
        *,
        fingerprint: str,
        gnupg_home: Path,
        executable: Path = Path("/usr/bin/gpg"),
        timeout_seconds: int = 15,
    ) -> None:
        fingerprint = fingerprint.strip().upper()
        if (
            len(fingerprint) not in {40, 64}
            or any(character not in "0123456789ABCDEF" for character in fingerprint)
            or not gnupg_home.is_absolute()
            or not executable.is_absolute()
            or not 1 <= timeout_seconds <= 30
        ):
            raise ValueError("exact verifier custody is required")
        self._fingerprint = fingerprint
        self._home = gnupg_home
        self._executable = executable
        self._timeout = timeout_seconds

    def _verify(self, payload: bytes, signature: str) -> bool:
        try:
            executable = os.stat(self._executable, follow_symlinks=False)
            home = os.stat(self._home, follow_symlinks=False)
            if (
                not stat.S_ISREG(executable.st_mode)
                or executable.st_mode & 0o022
                or not stat.S_ISDIR(home.st_mode)
                or home.st_uid != os.getuid()
                or home.st_mode & 0o077
            ):
                return False
            with tempfile.NamedTemporaryFile(
                prefix="skdashboard-signature-", suffix=".asc"
            ) as file:
                file.write(signature.encode("ascii"))
                file.flush()
                os.chmod(file.name, 0o600)
                result = subprocess.run(
                    (
                        str(self._executable),
                        "--homedir",
                        str(self._home),
                        "--batch",
                        "--no-auto-key-retrieve",
                        "--status-fd",
                        "1",
                        "--verify",
                        file.name,
                        "-",
                    ),
                    input=payload,
                    capture_output=True,
                    timeout=self._timeout,
                    check=False,
                )
            valid = f"[GNUPG:] VALIDSIG {self._fingerprint} "
            return result.returncode == 0 and valid.encode("ascii") in result.stdout
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            return False

    def verify_bytes(self, payload: bytes, signature: str) -> bool:
        return self._verify(payload, signature)

    def verify(self, token: SignedToken) -> bool:
        if token.payload.issuer.strip().upper() != self._fingerprint:
            return False
        try:
            payload = token.payload.model_dump_json().encode("utf-8")
        except Exception:
            return False
        return self._verify(payload, token.signature)


class FileTrustedIssuerBackend:
    """Reload and authenticate the exact value-free issuer policy per decision."""

    __slots__ = (
        "_expected_sha256",
        "_expected_signature_sha256",
        "_expected_uid",
        "_fingerprint",
        "_path",
        "_signature_path",
        "_verifier",
    )

    def __init__(
        self,
        *,
        path: Path,
        expected_sha256: str,
        signature_path: Path,
        expected_signature_sha256: str,
        fingerprint: str,
        verifier: DedicatedKeyringVerifier,
        expected_uid: int,
    ) -> None:
        self._path = path
        self._expected_sha256 = expected_sha256
        self._signature_path = signature_path
        self._expected_signature_sha256 = expected_signature_sha256
        self._fingerprint = fingerprint
        self._verifier = verifier
        self._expected_uid = expected_uid

    def snapshot(self) -> TrustedIssuerSnapshot:
        payload = _read_private_file(self._path, self._expected_sha256, self._expected_uid)
        signature = _read_private_file(
            self._signature_path,
            self._expected_signature_sha256,
            self._expected_uid,
        )
        if not self._verifier.verify_bytes(payload, signature.decode("ascii")):
            raise PermissionError("trusted issuer policy signature is invalid")
        value = json.loads(payload)
        expected_keys = {
            "audiences",
            "capabilities",
            "denials",
            "failover_identity_ids",
            "issuer",
            "policy_version",
            "principal_types",
            "schema_version",
            "scope_ceiling",
            "status",
        }
        if (
            type(value) is not dict
            or set(value) != expected_keys
            or value["schema_version"] != POLICY_SCHEMA
            or value["audiences"] != [AUDIENCE]
            or frozenset(value["capabilities"]) != CAPABILITIES
            or value["principal_types"] != [PRINCIPAL_KIND]
            or value["policy_version"] != POLICY_VERSION
            or frozenset(value["denials"]) != POLICY_DENIALS
            or value["scope_ceiling"] != "read_only"
            or value["status"] != "active_local_only"
            or value["failover_identity_ids"] != []
            or value["issuer"]
            != {
                "fingerprint": self._fingerprint,
                "host": "chiap08",
                "service": "skdashboard-read-only",
            }
        ):
            raise ValueError("trusted issuer policy is outside the exact binding")
        return TrustedIssuerSnapshot(
            policy_version=VERIFIER_POLICY_VERSION,
            revision=self._expected_sha256,
            issuers=(
                IssuerGrant(
                    fingerprint=self._fingerprint,
                    capabilities=CAPABILITIES,
                    audiences=frozenset({AUDIENCE}),
                    principal_kinds=frozenset({PRINCIPAL_KIND}),
                ),
            ),
        )


class ExactPrincipalBackend:
    """Accept only the approved current human operator."""

    __slots__ = ("_principal", "_revision")

    def __init__(self, principal: Principal, revision: str) -> None:
        if (
            principal.kind != PRINCIPAL_KIND
            or principal.principal_id != OPERATOR_ID
            or principal.subject != OPERATOR_ID
            or len(revision) != 64
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            raise ValueError("exact current human principal is required")
        self._principal = principal
        self._revision = revision

    def snapshot(self, principal: Principal) -> PrincipalPolicySnapshot:
        if principal != self._principal:
            raise PermissionError("principal is not approved")
        return PrincipalPolicySnapshot(
            revision=self._revision,
            principal=self._principal,
            active=True,
        )


class SQLiteCapabilityState:
    """Durable revocation, replay, and append-only sanitized audit state."""

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("capability state path must be absolute")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = os.stat(path.parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise PermissionError("capability state directory custody is invalid")
        if not path.exists():
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
        self._path = path
        self._validate_file()
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=DELETE;
                CREATE TABLE IF NOT EXISTS revocations (
                    credential_digest TEXT PRIMARY KEY,
                    revoked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS replay (
                    credential_digest TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    decision TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS audit_no_update
                BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete
                BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'append only'); END;
                """
            )
        self._validate_file()

    def _validate_file(self) -> None:
        metadata = os.stat(self._path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise PermissionError("capability state database custody is invalid")

    def _connect(self) -> sqlite3.Connection:
        self._validate_file()
        connection = sqlite3.connect(self._path, timeout=15, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def snapshot(self, credential_digests: tuple[str, ...]) -> RevocationSnapshot:
        with self._connect() as connection:
            rows = tuple(
                connection.execute(
                    "SELECT credential_digest, revoked_at FROM revocations ORDER BY credential_digest"
                )
            )
        all_digests = {row[0] for row in rows}
        revision = _sha256(
            json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        )
        return RevocationSnapshot(
            revision=revision,
            revoked_credential_digests=frozenset(all_digests.intersection(credential_digests)),
        )

    def reserve(
        self,
        *,
        credential_digest: str,
        decision_id: str,
        expires_at: datetime,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        expiry = expires_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM replay WHERE expires_at < ?", (now,))
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO replay VALUES (?, ?, ?)",
                    (credential_digest, decision_id, expiry),
                ).rowcount
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return inserted == 1

    def record(self, decision: AuthorizationDecision) -> None:
        payload = decision.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit(recorded_at, decision) VALUES (?, ?)",
                (datetime.now(timezone.utc).isoformat(), payload),
            )


def build(
    *,
    trusted_issuer_policy_file: Path,
    trusted_issuer_policy_sha256: str,
    trusted_issuer_signature_file: Path,
    trusted_issuer_signature_sha256: str,
    issuer_fingerprint: str,
    verifier_gnupg_home: Path,
    principal: Principal,
    principal_revision: str,
    state_db: Path,
    expected_uid: int,
) -> CapabilityAuthorizer:
    """Build the exact production authorizer or fail closed."""

    issuer_fingerprint = issuer_fingerprint.strip().upper()
    if issuer_fingerprint != ISSUER_FINGERPRINT:
        raise ValueError("the exact approved issuer fingerprint is required")
    verifier = DedicatedKeyringVerifier(
        fingerprint=issuer_fingerprint,
        gnupg_home=verifier_gnupg_home,
    )
    state = SQLiteCapabilityState(state_db)
    trusted = FileTrustedIssuerBackend(
        path=trusted_issuer_policy_file,
        expected_sha256=trusted_issuer_policy_sha256,
        signature_path=trusted_issuer_signature_file,
        expected_signature_sha256=trusted_issuer_signature_sha256,
        fingerprint=issuer_fingerprint,
        verifier=verifier,
        expected_uid=expected_uid,
    )
    trusted.snapshot()
    return CapabilityAuthorizer(
        trusted_issuers=trusted,
        principals=ExactPrincipalBackend(principal, principal_revision),
        revocations=state,
        replay=state,
        audit=state,
        signature_verifier=verifier,
    )


__all__ = [
    "DedicatedKeyringVerifier",
    "ExactPrincipalBackend",
    "FileTrustedIssuerBackend",
    "SQLiteCapabilityState",
    "build",
]
