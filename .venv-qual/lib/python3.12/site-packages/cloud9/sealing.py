"""
cloud9.sealing -- pluggable integrity-sealing backend for FEBs and seeds.

This module is an **additive scaffold**. It does NOT change how FEBs are
generated today: ``generator.py`` keeps writing the same ``integrity.checksum``
(``sha256:...``) and ``integrity.signature`` (``cloud9-sig-<md5>``) fields, and
``rehydrate_from_feb`` / ``validate_feb`` keep accepting every FEB that works
today. Nothing here is wired into the default generate or rehydrate path.

What it adds is a **seam**: a single ``Sealer`` interface plus a config-driven
resolver (``get_sealer``) so the move from the current classical sealing to a
real post-quantum *detached signature* (via ``sk_pgp``'s composite
**ML-DSA-87 + Ed448**) becomes a configuration change later, not a rewrite.

Backends
--------
- ``classical`` (default, always available) -- mirrors the current behaviour
  exactly: a SHA-256 content checksum plus the legacy ``cloud9-sig-<md5>``
  provenance tag. **Honest note:** that legacy tag is an MD5 over
  ``session-id / timestamp / intensity`` -- it is *not* a cryptographic
  signature over the FEB content and proves nothing about authorship. The only
  real tamper-evidence today is the SHA-256 checksum.
- ``sk_pgp`` (gated, opt-in) -- produces a genuine OpenPGP **detached**
  signature over the canonical FEB bytes using the composite
  ML-DSA-87 + Ed448 suite (FIPS 204 ML-DSA + RFC 8032 Ed448). A composite
  signature verifies **iff both legs verify** -- hybrid means *either leg still
  standing* keeps you safe, never "quantum-proof." This backend is inert unless
  ``sk_pgp`` is importable **and** a signing key is configured **and** it is
  explicitly selected; otherwise ``get_sealer`` falls back to ``classical``.

Honest-claims discipline (see docs/PQC_MIGRATION.md):
  ML-DSA / ML-KEM are **post-quantum / quantum-resistant**, NOT "quantum-proof"
  or "quantum-safe." The composite is a **hybrid** (lattice AND classical). All
  PQC assurance ultimately rests on sequoia-openpgp + liboqs via ``sk_pgp``;
  cloud9 adds no cryptography of its own.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Canonicalisation -- identical to generator.py so checksums match bit-for-bit
# --------------------------------------------------------------------------- #

CHECKSUM_PREFIX = "sha256:"
LEGACY_SIG_PREFIX = "cloud9-sig-"

# Signature schemes a sealer may advertise.
SCHEME_CLASSICAL = "classical"  # sha256 checksum + md5 provenance tag
SCHEME_SKPGP_MLDSA87_ED448 = "sk_pgp:mldsa87-ed448"  # composite L5 detached sig
SCHEME_SKPGP_MLDSA65_ED25519 = "sk_pgp:mldsa65-ed25519"  # composite L3 detached sig


def canonical_bytes(feb_like: Any) -> bytes:
    """Return the canonical byte string that integrity is computed over.

    Accepts a ``FEB`` (anything with ``model_dump``), a plain ``dict``, or raw
    ``bytes``/``str``. For FEB-shaped inputs the ``integrity`` block is excluded
    and keys are sorted -- exactly what ``generator.py`` hashes today, so a
    ``classical`` sealer reproduces the existing ``integrity.checksum``.
    """
    if isinstance(feb_like, (bytes, bytearray)):
        return bytes(feb_like)
    if isinstance(feb_like, str):
        return feb_like.encode("utf-8")
    if hasattr(feb_like, "model_dump"):
        data = feb_like.model_dump(exclude={"integrity"})
    elif isinstance(feb_like, dict):
        data = {k: v for k, v in feb_like.items() if k != "integrity"}
    else:  # pragma: no cover - defensive
        raise TypeError(f"cannot canonicalise {type(feb_like)!r}")
    return json.dumps(data, sort_keys=True).encode("utf-8")


def content_checksum(feb_like: Any) -> str:
    """The shared ``sha256:`` content checksum -- unchanged across all backends."""
    return CHECKSUM_PREFIX + hashlib.sha256(canonical_bytes(feb_like)).hexdigest()


# --------------------------------------------------------------------------- #
# Verdict + interface
# --------------------------------------------------------------------------- #


@dataclass
class SealVerdict:
    """Result of verifying a FEB's integrity.

    ``checksum_ok`` is the tamper-evidence that exists today. ``signature_ok``
    is tri-state: ``True`` (a real cryptographic signature verified), ``False``
    (a signature was present but failed), or ``None`` (no cryptographic
    signature to check -- the classical/legacy case). A legacy FEB therefore
    yields ``checksum_ok=True, signature_ok=None`` and is still treated as
    valid, never broken.
    """

    scheme: str
    checksum_ok: bool
    signature_ok: Optional[bool] = None
    fingerprint: Optional[str] = None
    is_post_quantum: bool = False
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # Honest gate: checksum must hold; a *present* signature must verify.
        return self.checksum_ok and self.signature_ok is not False


@runtime_checkable
class Sealer(Protocol):
    """Pluggable integrity backend. Implementations must be drop-in swappable."""

    scheme: str

    def available(self) -> bool: ...

    def checksum(self, feb_like: Any) -> str: ...

    def sign(self, feb_like: Any) -> Optional[str]:
        """Return a detached signature (armored str), or ``None`` if the backend
        does not produce a cryptographic signature (the classical case)."""

    def verify(
        self,
        feb_like: Any,
        signature: Optional[str],
        *,
        expected_checksum: Optional[str] = None,
    ) -> SealVerdict: ...


# --------------------------------------------------------------------------- #
# Classical backend -- the working path today (default)
# --------------------------------------------------------------------------- #


class ClassicalSealer:
    """Reproduces today's behaviour. Always available, no new dependencies.

    ``sign`` returns ``None`` because the current ``cloud9-sig-<md5>`` tag is a
    provenance marker, not a content signature -- so this sealer is honest about
    offering integrity (sha256) but **no** cryptographic authorship proof.
    """

    scheme = SCHEME_CLASSICAL

    def available(self) -> bool:
        return True

    def checksum(self, feb_like: Any) -> str:
        return content_checksum(feb_like)

    def sign(self, feb_like: Any) -> Optional[str]:  # noqa: ARG002 - by design
        return None

    @staticmethod
    def legacy_provenance_tag(
        session_id: str, created_at: str, intensity: float
    ) -> str:
        """The exact ``cloud9-sig-<md5>`` value generator.py writes today."""
        base = f"{session_id}-{created_at}-{intensity}"
        return LEGACY_SIG_PREFIX + hashlib.md5(base.encode("utf-8")).hexdigest()

    def verify(
        self,
        feb_like: Any,
        signature: Optional[str],
        *,
        expected_checksum: Optional[str] = None,
    ) -> SealVerdict:
        actual = content_checksum(feb_like)
        target = expected_checksum
        if target is None and isinstance(feb_like, dict):
            target = (feb_like.get("integrity") or {}).get("checksum")
        if target is None and hasattr(feb_like, "integrity"):
            target = getattr(feb_like.integrity, "checksum", None)
        checksum_ok = (target == actual) if target else True
        notes = []
        if target is None:
            notes.append("no checksum present to compare; integrity unverified")
        if signature and signature.startswith(LEGACY_SIG_PREFIX):
            notes.append(
                "legacy cloud9-sig tag is MD5 provenance, not a content signature"
            )
        return SealVerdict(
            scheme=self.scheme,
            checksum_ok=checksum_ok,
            signature_ok=None,  # no cryptographic signature in this backend
            notes=notes,
        )


# --------------------------------------------------------------------------- #
# sk_pgp backend -- gated, opt-in PQC detached signatures (the future swap)
# --------------------------------------------------------------------------- #


def _sk_pgp():
    """Import sk_pgp lazily; return the module or ``None`` if unavailable."""
    try:
        import sk_pgp  # type: ignore

        return sk_pgp
    except Exception:  # pragma: no cover - environment dependent
        return None


# Map our scheme tag -> sk_pgp cipher-suite string.
_SKPGP_SUITES = {
    SCHEME_SKPGP_MLDSA87_ED448: "mldsa87-ed448",
    SCHEME_SKPGP_MLDSA65_ED25519: "mldsa65-ed25519",
}


class SkPgpSealer:
    """Composite ML-DSA + Ed448/Ed25519 **detached** signing over FEB bytes.

    Scaffold status: the wiring is real and minimal, but this backend is INERT
    unless (a) ``sk_pgp`` imports, (b) a signing key + (optional) certificate are
    configured, and (c) it is explicitly selected via ``get_sealer``. Signatures
    are produced over :func:`canonical_bytes` and are intended to live in a
    **sidecar** (e.g. ``<feb>.sig``) so the FEB JSON itself -- and its existing
    sha256 checksum and JS cross-compatibility -- is never perturbed.

    Honest: a composite signature verifies iff **both** legs (lattice ML-DSA per
    FIPS 204 AND classical Ed448 per RFC 8032) verify; sequoia enforces the AND.
    This is *post-quantum / quantum-resistant*, not "quantum-proof."
    """

    def __init__(
        self,
        scheme: str = SCHEME_SKPGP_MLDSA87_ED448,
        *,
        secret_key_path: Optional[str] = None,
        cert_path: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        if scheme not in _SKPGP_SUITES:
            raise ValueError(f"unsupported sk_pgp scheme: {scheme!r}")
        self.scheme = scheme
        self.suite = _SKPGP_SUITES[scheme]
        self.secret_key_path = secret_key_path
        self.cert_path = cert_path
        self._password = password

    def available(self) -> bool:
        return _sk_pgp() is not None and bool(self.secret_key_path)

    def checksum(self, feb_like: Any) -> str:
        # Checksum stays identical across backends -- never diverge from classical.
        return content_checksum(feb_like)

    def _load_key(self):
        sk = _sk_pgp()
        if sk is None or not self.secret_key_path:
            raise RuntimeError(
                "sk_pgp backend not ready: package missing or no key configured"
            )
        # sk_pgp.Key.from_file(...) is the expected loader; kept thin + explicit
        # so the exact call is obvious when this is activated. Mirrors the README
        # surface: Key.generate / Key.sign_detached / Cert.verify_detached.
        return sk.Key.from_file(self.secret_key_path)  # type: ignore[attr-defined]

    def sign(self, feb_like: Any) -> Optional[str]:
        key = self._load_key()
        sig = key.sign_detached(canonical_bytes(feb_like), password=self._password)
        # sk_pgp returns armored *bytes*; normalise to str to honour the Sealer
        # contract and to live cleanly in a text sidecar (`<feb>.sig`).
        if isinstance(sig, (bytes, bytearray)):
            return bytes(sig).decode("utf-8")
        return sig

    def verify(
        self,
        feb_like: Any,
        signature: Optional[str],
        *,
        expected_checksum: Optional[str] = None,
    ) -> SealVerdict:
        sk = _sk_pgp()
        actual = content_checksum(feb_like)
        checksum_ok = (expected_checksum == actual) if expected_checksum else True
        if sk is None:
            return SealVerdict(
                scheme=self.scheme,
                checksum_ok=checksum_ok,
                signature_ok=None,
                notes=["sk_pgp unavailable; cannot verify PQC signature"],
            )
        if not signature:
            return SealVerdict(
                scheme=self.scheme,
                checksum_ok=checksum_ok,
                signature_ok=None,
                notes=["no detached signature supplied"],
            )
        cert_src = self.cert_path or self.secret_key_path
        if not cert_src:
            # Honest: a signature is present but we have no cert/key to check it.
            # Never reject — surface as unverifiable (signature_ok=None).
            return SealVerdict(
                scheme=self.scheme,
                checksum_ok=checksum_ok,
                signature_ok=None,
                notes=["signature present but no cert/key configured to verify it"],
            )
        try:
            # Prefer an explicit public cert; otherwise derive the cert from the
            # secret key so we never depend on Cert.from_file parsing secret files.
            if self.cert_path:
                cert = sk.Cert.from_file(self.cert_path)  # type: ignore[attr-defined]
            else:
                cert = sk.Key.from_file(self.secret_key_path).cert  # type: ignore[attr-defined]
            # sk_pgp's verify_detached takes the armored signature as *bytes*.
            sig_bytes = (
                signature.encode("utf-8") if isinstance(signature, str) else signature
            )
            sig_ok = bool(cert.verify_detached(sig_bytes, canonical_bytes(feb_like)))
        except Exception as exc:  # pragma: no cover - malformed sig/cert
            return SealVerdict(
                scheme=self.scheme,
                checksum_ok=checksum_ok,
                signature_ok=False,
                notes=[f"PQC verification raised: {exc}"],
            )
        return SealVerdict(
            scheme=self.scheme,
            checksum_ok=checksum_ok,
            signature_ok=sig_ok,  # True iff BOTH composite legs verify
            fingerprint=getattr(cert, "fingerprint", None),
            is_post_quantum=bool(getattr(cert, "is_post_quantum", True)),
        )


# --------------------------------------------------------------------------- #
# Resolver -- config is the only signal; default is always classical
# --------------------------------------------------------------------------- #

ENV_BACKEND = "CLOUD9_SEAL_BACKEND"  # "classical" (default) | "sk_pgp"
ENV_SCHEME = "CLOUD9_SEAL_SCHEME"  # e.g. "mldsa87-ed448" (sk_pgp only)
ENV_KEY = "CLOUD9_SEAL_KEY"  # path to armored secret key
ENV_CERT = "CLOUD9_SEAL_CERT"  # path to armored public cert (optional)
ENV_PASSWORD = "CLOUD9_SEAL_PASSWORD"  # passphrase (prefer gpg-agent later)

_SCHEME_BY_SUITE = {
    "mldsa87-ed448": SCHEME_SKPGP_MLDSA87_ED448,
    "mldsa65-ed25519": SCHEME_SKPGP_MLDSA65_ED25519,
}


def get_sealer(config: Optional[Dict[str, Any]] = None) -> Sealer:
    """Resolve the active sealer from config/env. **Defaults to classical.**

    Resolution order: explicit ``config`` dict -> environment -> classical. If
    the sk_pgp backend is requested but not actually ready (package missing or
    no key), this *honestly* falls back to the classical sealer rather than
    failing -- so enabling PQC can never break FEB generation.
    """
    config = config or {}
    backend = (
        config.get("backend") or os.environ.get(ENV_BACKEND) or "classical"
    ).lower()

    if backend in ("sk_pgp", "skpgp", "pqc"):
        suite = (
            config.get("scheme") or os.environ.get(ENV_SCHEME) or "mldsa87-ed448"
        ).lower()
        scheme = _SCHEME_BY_SUITE.get(suite, SCHEME_SKPGP_MLDSA87_ED448)
        sealer = SkPgpSealer(
            scheme=scheme,
            secret_key_path=config.get("key") or os.environ.get(ENV_KEY),
            cert_path=config.get("cert") or os.environ.get(ENV_CERT),
            password=config.get("password") or os.environ.get(ENV_PASSWORD),
        )
        if sealer.available():
            return sealer
        # Honest gate: requested but not ready -> stay on the working path.
    return ClassicalSealer()


def seal_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Introspection helper for CLI/diagnostics. Side-effect free.

    Reports the *active* sealer (after the honest classical fallback), what was
    *requested*, and whether the PQC backend is actually ready -- so an operator
    can tell at a glance whether enabling ``sk_pgp`` would sign or silently fall
    back to classical.
    """
    config = config or {}
    sealer = get_sealer(config)
    sk = _sk_pgp()
    requested_backend = (
        config.get("backend") or os.environ.get(ENV_BACKEND) or "classical"
    ).lower()
    key_configured = bool(config.get("key") or os.environ.get(ENV_KEY))
    cert_configured = bool(config.get("cert") or os.environ.get(ENV_CERT))
    wants_pqc = requested_backend in ("sk_pgp", "skpgp", "pqc")
    active_is_pq = sealer.scheme.startswith("sk_pgp:")
    return {
        "active_scheme": sealer.scheme,
        "active_is_post_quantum": active_is_pq,
        "requested_backend": requested_backend,
        "classical_available": True,
        "sk_pgp_importable": sk is not None,
        "sk_pgp_version": getattr(sk, "__version__", None) if sk else None,
        "key_configured": key_configured,
        "cert_configured": cert_configured,
        # Signing-ready: PQC requested, package present, signing key configured.
        "sk_pgp_ready": bool(wants_pqc and sk is not None and key_configured),
        # If PQC was asked for but we resolved to classical, we fell back.
        "fell_back_to_classical": bool(wants_pqc and not active_is_pq),
        "note": (
            "post-quantum / quantum-resistant via composite ML-DSA + EdDSA "
            "(FIPS 203/204) -- NOT quantum-proof; hybrid = valid iff both legs verify"
        ),
    }


# --------------------------------------------------------------------------- #
# Stage-2 wiring -- detached-signature sidecars (write side + read side)
# --------------------------------------------------------------------------- #
#
# A FEB's PQC signature lives in a sidecar file ``<feb>.sig`` (armored), never
# inside the FEB JSON. This keeps the sha256 checksum and JS/npm
# cross-compatibility bit-for-bit intact: a legacy reader simply ignores the
# sidecar, and the classical sealer writes no sidecar at all. Enabling PQC can
# therefore never alter an existing FEB or break its verification.

SIDECAR_SUFFIX = ".sig"


def sidecar_path_for(feb_path: Any) -> "os.PathLike[str] | str":
    """Return the sidecar path for a given FEB file path (``<feb>.sig``)."""
    return str(feb_path) + SIDECAR_SUFFIX


def get_verifier(config: Optional[Dict[str, Any]] = None) -> Sealer:
    """Resolve a sealer for **verification**. Defaults to classical.

    Verification needs only a public cert (or a key to derive it), not a usable
    signing key -- so this is intentionally laxer than :func:`get_sealer`
    (which gates on a configured secret key). If the sk_pgp backend is requested
    *or* a cert/key is configured, and ``sk_pgp`` imports, a ``SkPgpSealer`` is
    returned; otherwise classical. A ``SkPgpSealer`` returned here still yields
    an honest ``signature_ok=None`` verdict if it has nothing to verify against.
    """
    config = config or {}
    backend = (
        config.get("backend") or os.environ.get(ENV_BACKEND) or "classical"
    ).lower()
    key = config.get("key") or os.environ.get(ENV_KEY)
    cert = config.get("cert") or os.environ.get(ENV_CERT)
    want_pqc = backend in ("sk_pgp", "skpgp", "pqc") or bool(cert) or bool(key)
    if want_pqc and _sk_pgp() is not None:
        suite = (
            config.get("scheme") or os.environ.get(ENV_SCHEME) or "mldsa87-ed448"
        ).lower()
        scheme = _SCHEME_BY_SUITE.get(suite, SCHEME_SKPGP_MLDSA87_ED448)
        return SkPgpSealer(
            scheme=scheme,
            secret_key_path=key,
            cert_path=cert,
            password=config.get("password") or os.environ.get(ENV_PASSWORD),
        )
    return ClassicalSealer()


def write_seal(
    feb_like: Any,
    feb_path: Any,
    *,
    sealer: Optional[Sealer] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Stage-2 write side: optionally drop a ``<feb>.sig`` detached-signature sidecar.

    Resolves a sealer (``sealer`` arg wins, else :func:`get_sealer`). The
    classical default's ``sign()`` returns ``None`` -> **no sidecar is written
    and this returns ``None``**, so the on-disk result is byte-for-byte today's
    behaviour. Only when a real PQC backend is selected *and* ready does a
    signature get produced and written.

    Robustness: if signing raises (e.g. wrong passphrase), the FEB is left
    untouched and this returns ``None`` -- writing a FEB never fails because of
    sealing.

    Returns a small info dict (``signature_path`` / ``seal_scheme`` /
    ``seal_is_post_quantum``) when a sidecar was written, else ``None``.
    """
    sealer = sealer or get_sealer(config)
    try:
        sig = sealer.sign(feb_like)
    except Exception:  # never let sealing break FEB persistence
        return None
    if not sig:
        return None
    sig_path = sidecar_path_for(feb_path)
    try:
        with open(sig_path, "w", encoding="utf-8") as fh:
            fh.write(sig)
    except Exception:  # pragma: no cover - filesystem dependent
        return None
    return {
        "signature_path": str(sig_path),
        "seal_scheme": sealer.scheme,
        "seal_is_post_quantum": sealer.scheme.startswith("sk_pgp:"),
    }


def verify_seal(
    feb_like: Any,
    feb_path: Optional[Any] = None,
    *,
    signature: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    expected_checksum: Optional[str] = None,
) -> Optional[SealVerdict]:
    """Stage-3 read side: verify a sidecar signature **if one exists**.

    Returns ``None`` when there is no sidecar (and no inline ``signature``) --
    i.e. today's FEBs round-trip with **zero** behaviour change. When a sidecar
    is present, returns a :class:`SealVerdict`. This only ever *adds* assurance:
    a missing/unverifiable signature is reported honestly (``signature_ok``
    ``None``), never as a rejection; a present-but-failing signature is
    ``signature_ok=False`` and the caller decides policy.
    """
    sig = signature
    if sig is None and feb_path is not None:
        sig_path = sidecar_path_for(feb_path)
        if not os.path.exists(sig_path):
            return None
        try:
            with open(sig_path, "r", encoding="utf-8") as fh:
                sig = fh.read()
        except Exception:  # pragma: no cover - filesystem dependent
            return None
    if not sig:
        return None
    verifier = get_verifier(config)
    return verifier.verify(feb_like, sig, expected_checksum=expected_checksum)


__all__ = [
    "Sealer",
    "SealVerdict",
    "ClassicalSealer",
    "SkPgpSealer",
    "get_sealer",
    "get_verifier",
    "write_seal",
    "verify_seal",
    "sidecar_path_for",
    "seal_status",
    "content_checksum",
    "canonical_bytes",
    "SIDECAR_SUFFIX",
    "SCHEME_CLASSICAL",
    "SCHEME_SKPGP_MLDSA87_ED448",
    "SCHEME_SKPGP_MLDSA65_ED25519",
]
