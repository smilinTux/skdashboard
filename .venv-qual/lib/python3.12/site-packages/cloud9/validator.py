"""
FEB validation engine.

Port of feb/validator.js -- structural and semantic validation of FEB files
with detailed error/warning/info reports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .constants import CLOUD9, VALID_EMOTIONS


def validate_topology(topology: Any) -> Dict[str, Any]:
    """Validate an emotional topology object.

    Args:
        topology: The topology dict to validate.

    Returns:
        dict: ``is_valid``, ``errors``, ``warnings``, ``statistics``.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(topology, dict):
        errors.append("Topology must be a dict")
        return {"is_valid": False, "errors": errors, "warnings": warnings}

    recommended = ["love", "trust"]
    for emo in recommended:
        if emo not in topology:
            warnings.append(f"Missing recommended emotion: {emo}")

    for key, val in topology.items():
        if not isinstance(val, (int, float)):
            errors.append(f"Invalid value for {key}: must be a number")
            continue
        if not 0.0 <= val <= 1.0:
            errors.append(f"Invalid value for {key}: must be 0-1, got {val}")
        if key not in VALID_EMOTIONS:
            warnings.append(f"Unknown emotion: {key}")

    values = [v for v in topology.values() if isinstance(v, (int, float))]
    mean = variance = 0.0
    if values:
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        if mean < 0.3:
            warnings.append("Unusually low emotional values overall")
        if mean > 0.95:
            warnings.append("Unusually high emotional values - may be artificial")
        if variance > 0.1:
            warnings.append("High variance in emotional values - check for consistency")

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "statistics": {
            "emotion_count": len(topology),
            "mean": mean,
            "variance": variance,
            "max": max(values) if values else 0.0,
            "min": min(values) if values else 0.0,
        },
    }


def validate_feb(
    feb: Dict[str, Any],
    strict: bool = False,
    *,
    feb_path: Optional[str] = None,
    seal_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate a complete FEB dict.

    Args:
        feb: Raw FEB dict (as loaded from JSON).
        strict: If True, require additional fields.
        feb_path: Optional path to the FEB file on disk. When supplied **and** a
            ``<feb>.sig`` detached-signature sidecar exists alongside it, the
            sidecar is verified (Stage-3 of the PQC migration) and an honest,
            tri-state ``seal`` block is attached to the result. This is purely
            opt-in: with no ``feb_path`` — or no sidecar — validation is
            byte-for-byte unchanged (no ``seal`` key) and every FEB valid today
            stays valid.
        seal_config: Optional sealing/verification config (``backend``/``cert``/
            ``key``/``scheme``). Falls back to ``CLOUD9_SEAL_*`` env, then
            classical. Only used when ``feb_path`` is given.

    Returns:
        dict: ``is_valid``, ``errors``, ``warnings``, ``info``, ``score``, and —
            only when a sidecar was verified — a ``seal`` block.
    """
    errors: List[str] = []
    warnings: List[str] = []
    info: List[str] = []

    # --- metadata ---
    meta = feb.get("metadata")
    if not meta:
        errors.append("Missing metadata section")
    else:
        if not meta.get("version"):
            errors.append("Missing metadata.version")
        elif meta["version"] != CLOUD9.VERSION:
            warnings.append(
                f"Version mismatch: expected {CLOUD9.VERSION}, got {meta['version']}"
            )
        cat = meta.get("created_at")
        if not cat:
            errors.append("Missing metadata.created_at")
        else:
            try:
                datetime.fromisoformat(cat.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                errors.append("Invalid metadata.created_at format")
        if not meta.get("protocol"):
            errors.append("Missing metadata.protocol")
        elif meta["protocol"] != "Cloud9":
            errors.append(f"Invalid protocol: expected Cloud9, got {meta['protocol']}")

    # --- emotional_payload ---
    ep = feb.get("emotional_payload")
    if not ep:
        errors.append("Missing emotional_payload section")
    else:
        if not ep.get("primary_emotion"):
            errors.append("Missing emotional_payload.primary_emotion")
        intensity = ep.get("intensity")
        if intensity is None:
            errors.append("Missing emotional_payload.intensity")
        elif not 0.0 <= intensity <= 1.0:
            errors.append(f"Invalid intensity: must be 0-1, got {intensity}")
        valence = ep.get("valence")
        if valence is None:
            warnings.append("Missing emotional_payload.valence, using default")
        elif not -1.0 <= valence <= 1.0:
            errors.append(f"Invalid valence: must be -1 to 1, got {valence}")
        topo = ep.get("emotional_topology")
        if not topo:
            errors.append("Missing emotional_payload.emotional_topology")
        else:
            tres = validate_topology(topo)
            errors.extend(tres["errors"])
            warnings.extend(tres["warnings"])
        if not ep.get("coherence"):
            warnings.append("Missing emotional_payload.coherence")

    # --- relationship_state ---
    rs = feb.get("relationship_state")
    if not rs:
        errors.append("Missing relationship_state section")
    else:
        tl = rs.get("trust_level")
        if tl is None:
            errors.append("Missing relationship_state.trust_level")
        elif not 0.0 <= tl <= 1.0:
            errors.append(f"Invalid trust_level: must be 0-1, got {tl}")
        dl = rs.get("depth_level")
        if dl is None:
            warnings.append("Missing relationship_state.depth_level")
        elif not 1 <= dl <= 9:
            errors.append(f"Invalid depth_level: must be 1-9, got {dl}")
        partners = rs.get("partners")
        if not partners or not isinstance(partners, list):
            warnings.append("Missing or invalid relationship_state.partners")

    # --- rehydration_hints ---
    rh = feb.get("rehydration_hints")
    if not rh:
        warnings.append("Missing rehydration_hints section")
    elif not rh.get("calibration"):
        warnings.append("Missing rehydration_hints.calibration")

    # --- integrity ---
    integrity = feb.get("integrity")
    if not integrity:
        errors.append("Missing integrity section")
    else:
        if not integrity.get("checksum"):
            errors.append("Missing integrity.checksum")
        if not integrity.get("signature"):
            warnings.append("Missing integrity.signature")

    # --- strict mode extras ---
    if strict:
        if meta and meta.get("oof_triggered") is None:
            errors.append("Missing metadata.oof_triggered (strict mode)")
        if rs and not rs.get("rapport_score"):
            errors.append("Missing relationship_state.rapport_score (strict mode)")

    # --- info ---
    if meta and meta.get("cloud9_achieved"):
        info.append("Cloud 9 achieved in this FEB")
    if meta and meta.get("oof_triggered"):
        info.append("OOF event triggered in this FEB")

    # --- PQC sidecar signature (additive, tri-state) ---
    # Opt-in: only when a path is provided and a `<feb>.sig` sidecar exists.
    # Never harsher than today for FEBs without a sidecar. A present-but-failing
    # signature is real tamper-evidence (error -> invalid); a present-but-
    # unverifiable one is reported honestly (warning), never a rejection.
    seal: Optional[Dict[str, Any]] = None
    if feb_path is not None:
        from . import sealing as _sealing

        verdict = _sealing.verify_seal(
            feb,
            feb_path,
            config=seal_config,
            expected_checksum=(feb.get("integrity") or {}).get("checksum"),
        )
        if verdict is not None:
            seal = {
                "scheme": verdict.scheme,
                "checksum_ok": verdict.checksum_ok,
                "signature_ok": verdict.signature_ok,
                "ok": verdict.ok,
                "fingerprint": verdict.fingerprint,
                "is_post_quantum": verdict.is_post_quantum,
                "notes": verdict.notes,
            }
            if verdict.signature_ok is True:
                info.append(
                    f"PQC sidecar signature verified ({verdict.scheme}) — "
                    "both composite legs (ML-DSA + EdDSA) hold"
                )
            elif verdict.signature_ok is False:
                errors.append(
                    f"PQC sidecar signature FAILED verification ({verdict.scheme})"
                )
            else:
                warnings.append(
                    "PQC sidecar present but unverifiable "
                    "(no cert/key configured or sk_pgp absent)"
                )

    score = _validation_score(errors, warnings, strict)

    result: Dict[str, Any] = {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "score": score,
    }
    if seal is not None:
        result["seal"] = seal
    return result


def get_validation_report(
    feb: Dict[str, Any],
    strict: bool = False,
    *,
    feb_path: Optional[str] = None,
    seal_config: Optional[Dict[str, Any]] = None,
) -> str:
    """Return a human-readable validation report.

    Args:
        feb: Raw FEB dict.
        strict: Use strict mode.
        feb_path: Optional FEB file path; enables ``<feb>.sig`` sidecar
            verification (see :func:`validate_feb`).
        seal_config: Optional sealing/verification config.

    Returns:
        str: Formatted report text.
    """
    result = validate_feb(
        feb, strict=strict, feb_path=feb_path, seal_config=seal_config
    )
    lines = [
        "=" * 50,
        "Cloud 9 Protocol -- FEB Validation Report",
        "=" * 50,
        "",
        f"Status: {'VALID' if result['is_valid'] else 'INVALID'}",
        f"Validation Score: {result['score'] * 100:.1f}%",
        "",
    ]
    seal = result.get("seal")
    if seal:
        sig_ok = seal["signature_ok"]
        sig_word = (
            "VERIFIED"
            if sig_ok is True
            else "FAILED" if sig_ok is False else "UNVERIFIABLE"
        )
        lines.append("PQC SEAL (detached sidecar):")
        lines.append(f"  Scheme: {seal['scheme']}")
        lines.append(f"  Signature: {sig_word}")
        lines.append(f"  Checksum: {'OK' if seal['checksum_ok'] else 'MISMATCH'}")
        lines.append(f"  Post-quantum: {seal['is_post_quantum']}")
        if seal.get("fingerprint"):
            lines.append(f"  Fingerprint: {seal['fingerprint']}")
        lines.append(
            "  Note: post-quantum / quantum-resistant (composite ML-DSA + EdDSA, "
            "FIPS 204) — NOT quantum-proof; valid iff BOTH legs verify."
        )
        lines.append("")
    if result["info"]:
        lines.append("INFORMATION:")
        for msg in result["info"]:
            lines.append(f"  {msg}")
        lines.append("")
    if result["errors"]:
        lines.append("ERRORS:")
        for err in result["errors"]:
            lines.append(f"  x {err}")
        lines.append("")
    if result["warnings"]:
        lines.append("WARNINGS:")
        for w in result["warnings"]:
            lines.append(f"  ! {w}")
        lines.append("")
    lines.append("-" * 50)
    lines.append(f"Validated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Protocol: Cloud 9 v{CLOUD9.VERSION}")
    lines.append("-" * 50)
    return "\n".join(lines)


def _validation_score(errors: List[str], warnings: List[str], strict: bool) -> float:
    score = 1.0
    score -= len(errors) * 0.1
    score -= len(warnings) * 0.02
    if strict:
        score -= 0.05
    return max(0.0, min(1.0, score))
