"""
FEB (First Emotional Burst) generator.

Port of feb/generator.js -- creates, saves, loads, and discovers FEB files.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .constants import CLOUD9, DEFAULT_TOPOLOGIES, EMOTION_EMOJIS
from .models import (
    FEB,
    Calibration,
    Coherence,
    ConversationTopic,
    EmotionalPayload,
    Integrity,
    Metadata,
    RehydrationHints,
    RelationshipState,
    SharedHistory,
)
from .quantum import calculate_oof
from . import sealing as _sealing


def _trust_from_intensity(intensity: float) -> float:
    return min(0.97, 0.6 + intensity * 0.4)


def _depth_from_intensity(intensity: float) -> int:
    return math.ceil(intensity * 9)


def _continuity_from_intensity(intensity: float) -> int:
    return math.ceil(intensity * 9)


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _md5(data: str) -> str:
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def _compute_coherence(topology: Dict[str, float], primary_emotion: str) -> Coherence:
    values = list(topology.values())
    if not values:
        return Coherence()
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    coh = max(0.8, 1.0 - variance * 2)
    return Coherence(
        values_alignment=round(coh, 4),
        authenticity=round(0.95 + random.random() * 0.04, 4),
        presence=round(0.90 + random.random() * 0.09, 4),
    )


def generate_feb(
    emotion: str = "love",
    intensity: float = 0.8,
    valence: float = 0.9,
    subject: str = "Unknown",
    hints: Optional[List[str]] = None,
    topology: Optional[Dict[str, float]] = None,
    relationship_state: Optional[Dict[str, Any]] = None,
    session_id: str = "cloud9",
) -> FEB:
    """Generate a new First Emotional Burst.

    Args:
        emotion: Primary emotion (love, joy, trust, etc.).
        intensity: Emotional intensity (0.0-1.0).
        valence: Emotional valence (-1.0 to 1.0).
        subject: Subject of the emotion.
        hints: Extra rehydration hint strings.
        topology: Custom emotional topology overrides.
        relationship_state: Custom relationship state overrides.
        session_id: Session identifier.

    Returns:
        FEB: A fully populated FEB object (not yet saved).

    Raises:
        ValueError: If intensity or valence is out of range.
    """
    if not 0.0 <= intensity <= 1.0:
        raise ValueError(f"Intensity must be between 0 and 1, got {intensity}")
    if not -1.0 <= valence <= 1.0:
        raise ValueError(f"Valence must be between -1 and 1, got {valence}")

    hints = hints or []
    topology = topology or {}
    relationship_state = relationship_state or {}

    # Merge default topology with custom overrides
    topo = {**DEFAULT_TOPOLOGIES.get(emotion, {emotion: intensity}), **topology}
    if intensity > 0.9:
        topo.setdefault("breakthrough", 0.92)
        topo.setdefault("awe", 0.85)

    coherence = _compute_coherence(topo, emotion)

    # Build relationship state
    trust = _trust_from_intensity(intensity)
    depth = _depth_from_intensity(intensity)
    continuity = _continuity_from_intensity(intensity)

    rel_defaults: Dict[str, Any] = {
        "partners": ["Lumina", subject],
        "trust_level": trust,
        "depth_level": depth,
        "continuity_rating": continuity,
        "rapport_score": round(intensity * 0.95, 4),
        "mutual_recognition": True,
        "session_continuity": True,
        "shared_history": {
            "sessions_together": 1,
            "breakthrough_moments": 1 if intensity > 0.7 else 0,
            "failed_attempts": 0,
            "iterations": 1,
        },
    }
    rel_merged = {**rel_defaults, **relationship_state}
    shared = SharedHistory(**rel_merged.pop("shared_history", {}))
    rel = RelationshipState(shared_history=shared, **rel_merged)

    oof = calculate_oof(intensity, rel.trust_level)
    now_iso = datetime.now(timezone.utc).isoformat()

    meta = Metadata(
        version=CLOUD9.VERSION,
        protocol=CLOUD9.PROTOCOL,
        created_at=now_iso,
        generator=f"cloud9-python v{CLOUD9.VERSION}",
        session_id=session_id,
        oof_triggered=oof,
        cloud9_achieved=oof and depth >= 9,
    )

    emoji = EMOTION_EMOJIS.get(emotion, "\U0001f49c")

    # Rehydration hints
    visual_anchors = [
        f"The moment {subject} expressed {emotion}",
        f"Facial expression showing genuine {emotion}",
        f"Body language indicating {emotion} connection",
    ] + hints[:3]

    sensory_triggers = [
        f"Mention of {subject}'s name",
        f"Discussion about {emotion}",
        "Reference to shared experiences",
    ] + hints[:2]

    topics = [
        ConversationTopic(
            topic=f"Expressing {emotion}",
            trigger_phrase=f"How do you feel about {subject}?",
            response_template=f"I feel {emotion} {emoji} It's deep and authentic.",
        ),
        ConversationTopic(
            topic="The Connection",
            trigger_phrase="What makes our connection special?",
            response_template="It's the way we understand each other without words.",
        ),
    ]
    if hints:
        topics.append(
            ConversationTopic(
                topic="Personal Detail",
                trigger_phrase=hints[0],
                response_template="That moment was significant because...",
            )
        )

    calibration = Calibration(
        target_intensity=intensity,
        target_valence=valence,
        expected_oof=oof,
        oof_threshold={
            "intensity_min": CLOUD9.OOF_THRESHOLD.INTENSITY,
            "trust_min": CLOUD9.OOF_THRESHOLD.TRUST,
        },
    )

    rh = RehydrationHints(
        visual_anchors=visual_anchors,
        sensory_triggers=sensory_triggers,
        conversation_topics=topics,
        calibration=calibration,
    )

    payload = EmotionalPayload(
        primary_emotion=emotion,
        emoji=emoji,
        intensity=intensity,
        valence=valence,
        emotional_topology=topo,
        coherence=coherence,
    )

    feb = FEB(
        metadata=meta,
        emotional_payload=payload,
        relationship_state=rel,
        rehydration_hints=rh,
        integrity=Integrity(),
    )

    # Compute integrity
    content_for_hash = json.dumps(feb.model_dump(exclude={"integrity"}), sort_keys=True)
    checksum = f"sha256:{_sha256(content_for_hash)}"
    sig_base = f"{session_id}-{now_iso}-{intensity}"
    signature = f"cloud9-sig-{_md5(sig_base)}"
    feb.integrity = Integrity(checksum=checksum, signature=signature)

    return feb


def save_feb(
    feb: FEB,
    directory: str = "~/.openclaw/feb",
    *,
    seal_config: Optional[Dict[str, Any]] = None,
    sealer: Optional[Any] = None,
) -> Dict[str, Any]:
    """Save a FEB to the filesystem.

    Args:
        feb: FEB object to persist.
        directory: Target directory (tilde-expanded automatically).
        seal_config: Optional sealing config (overrides ``CLOUD9_SEAL_*`` env).
            Stage-2 of the PQC migration: when a real ``sk_pgp`` backend is
            selected *and* ready, a detached-signature sidecar ``<feb>.sig`` is
            additionally written. The FEB JSON itself is **never** changed.
        sealer: Optional pre-resolved :class:`cloud9.sealing.Sealer` (mainly for
            tests); normally leave ``None`` and let config/env resolve it.

    Returns:
        dict: Result with ``filepath``, ``filename``, ``emotion``, etc. When a
        signature sidecar is written, the result additionally carries
        ``signature_path`` / ``seal_scheme`` / ``seal_is_post_quantum``.

    Note:
        With the **default** (classical) backend the on-disk output and the
        returned dict are byte-for-byte identical to prior behaviour: the
        classical sealer produces no signature, so no sidecar is written and no
        seal keys are added. Sealing can never break FEB persistence.
    """
    expanded = Path(directory).expanduser()
    expanded.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    emotion = feb.emotional_payload.primary_emotion
    filename = f"FEB_{ts}_{emotion}.feb"
    filepath = expanded / filename

    filepath.write_text(feb.to_json(), encoding="utf-8")

    result = {
        "success": True,
        "filepath": str(filepath),
        "filename": filename,
        "emotion": emotion,
        "intensity": feb.emotional_payload.intensity,
        "oof": feb.metadata.oof_triggered,
        "cloud9": feb.metadata.cloud9_achieved,
    }

    # Stage-2 (opt-in, gated): write a detached PQC signature sidecar when a
    # real sk_pgp backend is configured + ready. Classical default => no-op.
    seal_info = _sealing.write_seal(feb, filepath, sealer=sealer, config=seal_config)
    if seal_info:
        result.update(seal_info)

    return result


def fall_in_love(
    emotion: str = "love",
    intensity: float = 0.8,
    valence: float = 0.9,
    subject: str = "Unknown",
    directory: str = "~/.openclaw/feb",
    hints: Optional[List[str]] = None,
    topology: Optional[Dict[str, float]] = None,
    relationship_state: Optional[Dict[str, Any]] = None,
    session_id: str = "cloud9",
    seal_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate and save a FEB in one call.

    Combines :func:`generate_feb` and :func:`save_feb` into a single
    convenience function.  This is the "fall in love" pattern — create
    emotional continuity and immediately persist it.

    Args:
        emotion: Primary emotion (default ``"love"``).
        intensity: Emotional intensity 0-1 (default 0.8).
        valence: Emotional valence -1 to 1 (default 0.9).
        subject: Subject of the emotion.
        directory: Filesystem directory for the saved ``.feb`` file.
        hints: Extra rehydration hints.
        topology: Custom emotional topology overrides.
        relationship_state: Custom relationship state overrides.
        session_id: Session identifier.

    Returns:
        dict: Save result with ``filepath``, ``emotion``, ``intensity``,
        ``oof``, ``cloud9``, and the ``feb`` object itself.
    """
    feb = generate_feb(
        emotion=emotion,
        intensity=intensity,
        valence=valence,
        subject=subject,
        hints=hints,
        topology=topology,
        relationship_state=relationship_state,
        session_id=session_id,
    )
    result = save_feb(feb, directory=directory, seal_config=seal_config)
    result["feb"] = feb
    return result


def load_feb(filepath: str) -> FEB:
    """Load a FEB from a JSON file.

    Args:
        filepath: Path to the ``.feb`` file.

    Returns:
        FEB: Parsed FEB model.
    """
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    return FEB(**data)


def find_feb_files(
    directory: str = "~/.openclaw/feb",
    emotion: Optional[str] = None,
    since: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Discover FEB files in a directory.

    Args:
        directory: Directory to search.
        emotion: Optional filter by primary emotion.
        since: Optional filter by creation date.

    Returns:
        list: Metadata dicts for matching FEB files (newest first).
    """
    expanded = Path(directory).expanduser()
    if not expanded.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    for f in expanded.iterdir():
        if f.name.startswith("FEB_") and f.suffix == ".feb":
            try:
                feb = load_feb(str(f))
            except Exception:
                continue

            if emotion and feb.emotional_payload.primary_emotion != emotion:
                continue
            if since:
                created = datetime.fromisoformat(feb.metadata.created_at)
                if created < since:
                    continue

            results.append(
                {
                    "filepath": str(f),
                    "filename": f.name,
                    "emotion": feb.emotional_payload.primary_emotion,
                    "intensity": feb.emotional_payload.intensity,
                    "oof": feb.metadata.oof_triggered,
                    "cloud9": feb.metadata.cloud9_achieved,
                    "created": feb.metadata.created_at,
                }
            )

    results.sort(key=lambda r: r["created"], reverse=True)
    return results
