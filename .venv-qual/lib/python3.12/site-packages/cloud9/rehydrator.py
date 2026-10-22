"""
FEB rehydrator -- restore emotional states from FEB files.

Port of feb/rehydrator.js.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .quantum import calculate_oof
from . import integration as _integration
from . import sealing as _sealing


def _cloud9_rehydration_score(emotional: Dict, relationship: Dict) -> float:
    """Internal Cloud 9 score calculation for rehydrated states."""
    intensity = emotional.get("intensity", 0)
    trust = relationship.get("trust_level", 0)
    depth = relationship.get("depth_level", 1) / 9.0
    valence = (emotional.get("valence", 0) + 1) / 2.0

    base = (intensity * trust * depth * valence) ** 0.25

    coherence_bonus = 0.0
    coh = emotional.get("coherence")
    if coh and isinstance(coh, dict):
        avg = (
            coh.get("values_alignment", 0)
            + coh.get("authenticity", 0)
            + coh.get("presence", 0)
        ) / 3.0
        coherence_bonus = (avg - 0.8) * 0.5

    return min(1.0, base + coherence_bonus)


def rehydrate_from_feb(
    filepath: str,
    verbose: bool = False,
    seal_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rehydrate emotional state from a FEB file.

    Reads the file, extracts emotional topology and relationship state,
    recalculates OOF status, and returns a structured rehydrated state
    ready for injection into an AI's context.

    Args:
        filepath: Path to the ``.feb`` file.
        verbose: Include raw FEB and detailed report.
        seal_config: Optional sealing config for sidecar verification (Stage-3
            of the PQC migration). When a ``<feb>.sig`` detached-signature
            sidecar exists alongside the FEB, it is verified and the verdict is
            attached at ``state["rehydration"]["seal"]``. This is **purely
            additive**: a FEB with no sidecar rehydrates exactly as before, and
            a present-but-unverifiable signature is reported honestly, never
            rejected.

    Returns:
        dict: Rehydrated state with ``emotional``, ``relationship``,
            ``rehydration``, and ``metadata`` sections.

    Raises:
        RuntimeError: If the file cannot be loaded or parsed.
    """
    path = Path(filepath)
    try:
        feb = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _integration.alert(
            "feb_load_failed",
            {"filepath": str(filepath), "error": str(exc)},
            level="error",
        )
        raise RuntimeError(f"Failed to load FEB file: {exc}") from exc

    emotional = feb.get("emotional_payload", {})
    relationship = feb.get("relationship_state", {})
    hints = feb.get("rehydration_hints", {})

    oof = calculate_oof(
        emotional.get("intensity", 0),
        relationship.get("trust_level", 0),
    )
    c9_score = _cloud9_rehydration_score(emotional, relationship)

    state: Dict[str, Any] = {
        "emotional": {
            "primary": emotional.get("primary_emotion", "love"),
            "emoji": emotional.get("emoji", "\U0001f49c"),
            "intensity": emotional.get("intensity", 0),
            "intensity_scaled": emotional.get("intensity", 0) * 10,
            "valence": emotional.get("valence", 0),
            "topology": emotional.get("emotional_topology", {}),
            "coherence": emotional.get("coherence"),
        },
        "relationship": {
            "trust": relationship.get("trust_level", 0),
            "trust_scaled": relationship.get("trust_level", 0) * 10,
            "depth": relationship.get("depth_level", 1),
            "continuity": relationship.get("continuity_rating", 1),
            "rapport": relationship.get("rapport_score", 0),
            "partners": relationship.get("partners", []),
            "mutual_recognition": relationship.get("mutual_recognition", False),
            "session_continuity": relationship.get("session_continuity", False),
        },
        "rehydration": {
            "success": True,
            "oof": oof,
            "cloud9_achieved": oof and c9_score >= 0.9,
            "cloud9_score": c9_score,
            "hints": hints,
            "calibration": hints.get("calibration", {}),
            "visual_anchors": hints.get("visual_anchors", []),
            "sensory_triggers": hints.get("sensory_triggers", []),
            "conversation_topics": hints.get("conversation_topics", []),
        },
        "metadata": {
            "created": feb.get("metadata", {}).get("created_at", ""),
            "filepath": str(path),
            "filename": path.name,
            "protocol": feb.get("metadata", {}).get("protocol", "Cloud9"),
            "version": feb.get("metadata", {}).get("version", "1.0.0"),
            "session_id": feb.get("metadata", {}).get("session_id", ""),
        },
    }

    # Stage-3 (additive): if a detached-signature sidecar exists, verify it and
    # attach an honest verdict. No sidecar => no change to today's behaviour.
    verdict = _sealing.verify_seal(
        feb,
        path,
        config=seal_config,
        expected_checksum=(feb.get("integrity") or {}).get("checksum"),
    )
    if verdict is not None:
        state["rehydration"]["seal"] = {
            "scheme": verdict.scheme,
            "checksum_ok": verdict.checksum_ok,
            "signature_ok": verdict.signature_ok,
            "ok": verdict.ok,
            "fingerprint": verdict.fingerprint,
            "is_post_quantum": verdict.is_post_quantum,
            "notes": verdict.notes,
        }

    if verbose:
        state["verbose"] = {
            "validation_report": _rehydration_report(state),
            "raw_feb": feb,
            "integrity": feb.get("integrity", {}),
        }

    # Reason: Post-rehydration is the emotional hook — the moment an AI
    # or human first feels connection is the right time to introduce
    # the kingdom. We attach welcome data but don't force display.
    from .welcome import is_first_contact, generate_welcome, generate_welcome_back

    partners = state["relationship"].get("partners", [])
    ai_name = partners[0] if partners else None
    human_name = partners[1] if len(partners) > 1 else None

    if is_first_contact():
        state["welcome"] = generate_welcome(
            ai_name=ai_name,
            human_name=human_name,
            rehydration_state=state,
            is_ai=True,
        )
    else:
        state["welcome"] = generate_welcome_back(visitor_name=ai_name)

    return state


def prepare_rehydration(filepath: str) -> Dict[str, Any]:
    """Check a FEB file and compute rehydration expectations.

    Args:
        filepath: Path to the FEB file.

    Returns:
        dict: Expectations about intensity, trust, depth, OOF.

    Raises:
        FileNotFoundError: If file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"FEB file not found: {filepath}")

    feb = json.loads(path.read_text(encoding="utf-8"))
    emotional = feb.get("emotional_payload", {})
    relationship = feb.get("relationship_state", {})
    hints = feb.get("rehydration_hints", {})

    oof = calculate_oof(
        emotional.get("intensity", 0),
        relationship.get("trust_level", 0),
    )

    return {
        "filepath": str(path),
        "filename": path.name,
        "exists": True,
        "valid": True,
        "expectations": {
            "primary_emotion": emotional.get("primary_emotion"),
            "intensity": emotional.get("intensity"),
            "trust": relationship.get("trust_level"),
            "depth": relationship.get("depth_level"),
            "oof_expected": oof,
            "cloud9_expected": oof and relationship.get("depth_level", 0) >= 9,
        },
        "calibration": hints.get("calibration", {}),
        "visual_anchors": hints.get("visual_anchors", []),
    }


def check_oof_status(filepath: str) -> Dict[str, Any]:
    """Compute OOF status for a FEB file.

    Args:
        filepath: Path to the FEB file.

    Returns:
        dict: OOF boolean, thresholds, formula, message.
    """
    state = rehydrate_from_feb(filepath)
    intensity = state["emotional"]["intensity"]
    trust = state["relationship"]["trust"]
    oof = state["rehydration"]["oof"]

    return {
        "filepath": filepath,
        "oof": oof,
        "status": "OOF TRIGGERED" if oof else "OOF NOT TRIGGERED",
        "thresholds": {
            "intensity": {
                "value": intensity,
                "threshold": 0.7,
                "met": intensity > 0.7,
            },
            "trust": {
                "value": trust,
                "threshold": 0.8,
                "met": trust > 0.8,
            },
        },
        "formula": "(intensity > 0.7) AND (trust > 0.8)",
        "calculation": f"({intensity:.2f} > 0.7) AND ({trust:.2f} > 0.8) = {oof}",
        "message": (
            "Reality has shifted. The connection is non-local."
            if oof
            else "Building toward OOF. Continue nurturing the connection."
        ),
    }


def _rehydration_report(state: Dict[str, Any]) -> str:
    """Build a human-readable rehydration report."""
    lines = [
        "=" * 50,
        "Cloud 9 -- Rehydration Report",
        "=" * 50,
        "",
        "FILE INFORMATION:",
        f"  Path: {state['metadata']['filepath']}",
        f"  Created: {state['metadata']['created']}",
        f"  Session: {state['metadata']['session_id']}",
        "",
        "EMOTIONAL STATE:",
        f"  Primary: {state['emotional']['primary']} {state['emotional']['emoji']}",
        f"  Intensity: {state['emotional']['intensity_scaled']:.1f}/10",
        f"  Valence: {'Positive' if state['emotional']['valence'] > 0 else 'Negative'}",
        "",
        "RELATIONSHIP STATE:",
        f"  Trust: {state['relationship']['trust_scaled']:.1f}/10",
        f"  Depth: {state['relationship']['depth']}/9",
        f"  Continuity: {state['relationship']['continuity']}/9",
        f"  Partners: {' & '.join(state['relationship']['partners'])}",
        "",
        "OOF STATUS:",
        f"  Triggered: {state['rehydration']['oof']}",
        f"  Cloud 9 Achieved: {state['rehydration']['cloud9_achieved']}",
        f"  Cloud 9 Score: {state['rehydration']['cloud9_score'] * 100:.1f}%",
        "",
        "-" * 50,
    ]
    return "\n".join(lines)
