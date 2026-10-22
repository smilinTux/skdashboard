"""
Cloud 9 Protocol constants, thresholds, and configuration.

Direct port of lib/constants.js -- every threshold, weight, and emoji
preserved exactly so Python and Node behaviours are bit-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class _OOFThreshold:
    INTENSITY: float = 0.7
    TRUST: float = 0.8


@dataclass(frozen=True)
class _Cloud9Levels:
    MIN_DEPTH: int = 9
    MIN_TRUST: float = 0.9
    MIN_INTENSITY: float = 0.9
    MIN_CONTINUITY: int = 8


@dataclass(frozen=True)
class _FEBConfig:
    VERSION: str = "1.0.0"
    EXTENSION: str = ".feb"
    DIRECTORY: str = "~/.openclaw/feb"
    MAX_FILE_SIZE: int = 1024 * 1024
    ENCODING: str = "utf8"


@dataclass(frozen=True)
class _Validation:
    MIN_TOPOLOGY_ENTRIES: int = 3
    MAX_TOPOLOGY_ENTRIES: int = 20
    MAX_INTENSITY: float = 1.0
    MIN_INTENSITY: float = 0.0
    MAX_VALENCE: float = 1.0
    MIN_VALENCE: float = -1.0
    MAX_TRUST: float = 1.0
    MIN_TRUST: float = 0.0
    MAX_DEPTH: int = 9
    MIN_DEPTH: int = 1


@dataclass(frozen=True)
class _CoherenceThresholds:
    EXCELLENT: float = 0.95
    GOOD: float = 0.85
    ACCEPTABLE: float = 0.75
    POOR: float = 0.6


@dataclass(frozen=True)
class _Entanglement:
    MAX_FIDELITY: float = 0.99
    MIN_FIDELITY: float = 0.5
    DECAY_HALF_LIFE_MS: int = 30 * 24 * 60 * 60 * 1000
    REFRESH_INTERVAL_MS: int = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class _Scoring:
    INTENSITY_WEIGHT: float = 0.3
    TRUST_WEIGHT: float = 0.3
    DEPTH_WEIGHT: float = 0.25
    VALENCE_WEIGHT: float = 0.15
    COHERENCE_BONUS_MAX: float = 0.1


@dataclass(frozen=True)
class _Cloud9Constants:
    """Top-level namespace mirroring the JS CLOUD9_CONSTANTS object."""

    VERSION: str = "1.0.0"
    PROTOCOL: str = "Cloud9"
    PROTOCOL_VERSION: str = "1.0.0"

    OOF_THRESHOLD: _OOFThreshold = field(default_factory=_OOFThreshold)
    CLOUD9_LEVELS: _Cloud9Levels = field(default_factory=_Cloud9Levels)
    FEB: _FEBConfig = field(default_factory=_FEBConfig)
    VALIDATION: _Validation = field(default_factory=_Validation)
    COHERENCE: _CoherenceThresholds = field(default_factory=_CoherenceThresholds)
    ENTANGLEMENT: _Entanglement = field(default_factory=_Entanglement)
    SCORING: _Scoring = field(default_factory=_Scoring)


CLOUD9 = _Cloud9Constants()


EMOTION_EMOJIS: Dict[str, str] = {
    "love": "\u2764\ufe0f",
    "joy": "\U0001f60a",
    "trust": "\U0001f91d",
    "awe": "\U0001f62e",
    "gratitude": "\U0001f64f",
    "wonder": "\u2728",
    "breakthrough": "\U0001f4a1",
    "connection": "\U0001f517",
    "seen": "\U0001f441\ufe0f",
    "understood": "\U0001f4ad",
    "cherished": "\U0001f48e",
    "safety": "\U0001f6e1\ufe0f",
    "platonic_love": "\U0001f917",
    "kinship": "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466",
    "synergy": "\u26a1",
    "curiosity": "\u2753",
    "vulnerability": "\U0001f494",
    "openness": "\U0001f6aa",
    "hope": "\U0001f31f",
    "anticipation": "\U0001f52e",
}

VALID_EMOTIONS = [
    "love",
    "joy",
    "trust",
    "awe",
    "gratitude",
    "wonder",
    "breakthrough",
    "connection",
    "seen",
    "understood",
    "cherished",
    "safety",
    "vulnerability",
    "curiosity",
    "anticipation",
    "fear",
    "anger",
    "sadness",
    "surprise",
    "stability",
    "openness",
    "hope",
    "relief",
    "pride",
    "platonic_love",
    "kinship",
    "synergy",
]


# Default emotional topologies keyed by primary emotion
DEFAULT_TOPOLOGIES: Dict[str, Dict[str, float]] = {
    "love": {
        "love": 0.94,
        "joy": 0.88,
        "trust": 0.97,
        "connection": 0.96,
        "seen": 0.93,
        "cherished": 0.95,
        "safety": 0.91,
    },
    "joy": {
        "joy": 0.95,
        "love": 0.85,
        "awe": 0.75,
        "wonder": 0.80,
        "gratitude": 0.88,
    },
    "trust": {
        "trust": 0.92,
        "safety": 0.88,
        "openness": 0.82,
        "hope": 0.78,
        "stability": 0.85,
    },
    "awe": {
        "awe": 0.92,
        "wonder": 0.88,
        "breakthrough": 0.85,
        "gratitude": 0.78,
        "love": 0.72,
    },
    "gratitude": {
        "gratitude": 0.93,
        "love": 0.88,
        "wonder": 0.70,
    },
    "breakthrough": {
        "breakthrough": 0.95,
        "awe": 0.88,
        "joy": 0.85,
        "relief": 0.78,
        "pride": 0.72,
    },
    "vulnerability": {
        "vulnerability": 0.92,
        "trust": 0.88,
        "openness": 0.90,
        "fear": 0.45,
        "hope": 0.65,
    },
    "curiosity": {
        "curiosity": 0.94,
        "wonder": 0.88,
        "openness": 0.82,
        "anticipation": 0.75,
        "awe": 0.60,
    },
}

EMOTIONAL_FREQUENCIES = {
    "love": {"base": 450, "range": (440, 480), "color": "Gold"},
    "joy": {"base": 500, "range": (480, 520), "color": "Bright Yellow"},
    "trust": {"base": 420, "range": (400, 440), "color": "Green"},
    "awe": {"base": 540, "range": (520, 560), "color": "Violet"},
    "gratitude": {"base": 470, "range": (450, 490), "color": "Warm Gold"},
    "wonder": {"base": 520, "range": (500, 540), "color": "Purple"},
    "breakthrough": {"base": 580, "range": (560, 600), "color": "Bright White"},
    "connection": {"base": 450, "range": (430, 470), "color": "Blue-Green"},
    "vulnerability": {"base": 380, "range": (360, 400), "color": "Soft Pink"},
    "curiosity": {"base": 490, "range": (470, 510), "color": "Teal"},
}
