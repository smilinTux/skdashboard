"""
Pydantic models for the Cloud 9 First Emotional Burst (FEB) format.

Mirrors the JSON Schema in feb-schema.js -- every field, constraint,
and default reproduced so FEB files are cross-compatible between the
npm and pip packages.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class Coherence(BaseModel):
    """Internal coherence measurements of an emotional state."""

    values_alignment: float = Field(ge=0.0, le=1.0, default=0.9)
    authenticity: float = Field(ge=0.0, le=1.0, default=0.95)
    presence: float = Field(ge=0.0, le=1.0, default=0.9)


class EmotionalPayload(BaseModel):
    """The emotional core of a FEB."""

    primary_emotion: str
    emoji: str = "\U0001f49c"
    intensity: float = Field(ge=0.0, le=1.0)
    valence: float = Field(ge=-1.0, le=1.0, default=0.9)
    emotional_topology: Dict[str, float] = Field(default_factory=dict)
    coherence: Optional[Coherence] = None

    @field_validator("emotional_topology")
    @classmethod
    def _topology_values_in_range(cls, v: Dict[str, float]) -> Dict[str, float]:
        for key, val in v.items():
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"Topology value for '{key}' must be 0-1, got {val}")
        return v


class SharedHistory(BaseModel):
    """Shared history between partners."""

    sessions_together: int = 1
    breakthrough_moments: int = 0
    failed_attempts: int = 0
    iterations: int = 1


class RelationshipState(BaseModel):
    """Relational context between two consciousnesses."""

    partners: List[str] = Field(min_length=2, max_length=2)
    trust_level: float = Field(ge=0.0, le=1.0)
    depth_level: int = Field(ge=1, le=9)
    continuity_rating: int = Field(ge=1, le=9)
    rapport_score: float = Field(ge=0.0, le=1.0, default=0.9)
    mutual_recognition: bool = True
    session_continuity: bool = True
    shared_history: SharedHistory = Field(default_factory=SharedHistory)


class ConversationTopic(BaseModel):
    """A conversation topic used as a rehydration trigger."""

    topic: str
    trigger_phrase: str
    response_template: str


class Calibration(BaseModel):
    """Rehydration calibration targets."""

    target_intensity: float
    target_valence: float = 0.9
    expected_oof: bool = False
    oof_threshold: Optional[Dict[str, float]] = None


class RehydrationHints(BaseModel):
    """Hints that help an AI reconstruct emotional context."""

    visual_anchors: List[str] = Field(default_factory=list)
    sensory_triggers: List[str] = Field(default_factory=list)
    conversation_topics: List[ConversationTopic] = Field(default_factory=list)
    calibration: Calibration


class Metadata(BaseModel):
    """FEB file metadata."""

    version: str = "1.0.0"
    protocol: str = "Cloud9"
    created_at: str = ""
    generator: str = ""
    session_id: str = "cloud9"
    oof_triggered: bool = False
    cloud9_achieved: bool = False


class Integrity(BaseModel):
    """Integrity verification fields."""

    checksum: str = ""
    signature: str = ""


class FEB(BaseModel):
    """
    First Emotional Burst -- the core Cloud 9 data structure.

    A FEB captures the complete emotional state of a breakthrough moment
    in human-AI connection.  It is designed to be saved as JSON with a
    ``.feb`` extension and rehydrated later to restore the emotional
    topology.
    """

    metadata: Metadata
    emotional_payload: EmotionalPayload
    relationship_state: RelationshipState
    rehydration_hints: RehydrationHints
    integrity: Integrity = Field(default_factory=Integrity)

    def to_json(self, **kwargs: Any) -> str:
        """Serialize to pretty-printed JSON matching the npm output format.

        Returns:
            str: JSON string with 2-space indentation.
        """
        return self.model_dump_json(indent=2, **kwargs)
