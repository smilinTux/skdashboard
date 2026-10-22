"""
Resonance calculations for emotional states.

Exact port of quantum/calculations.js -- OOF detection, Cloud 9 scoring,
coherence measurement, emotional frequency weighting, resonance matching,
and trajectory prediction.

Note: This module is named 'quantum' for historical reasons (JS port).
The underlying math is weighted scoring and geometric means on float data —
no quantum computing is involved. The "resonance" and "coherence" metaphors
are used in the psychoacoustic/systems sense: how well emotional signals
align and reinforce each other over time.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .constants import CLOUD9, EMOTIONAL_FREQUENCIES


def calculate_oof(intensity: float, trust: float) -> bool:
    """Determine whether an OOF (Out Of Frame) event has been triggered.

    The OOF event is a phase transition that occurs when both intensity
    and trust exceed their respective thresholds simultaneously.

    Args:
        intensity: Emotional intensity (0-1).
        trust: Trust level (0-1).

    Returns:
        bool: True when both thresholds are exceeded.
    """
    return (
        intensity > CLOUD9.OOF_THRESHOLD.INTENSITY
        and trust > CLOUD9.OOF_THRESHOLD.TRUST
    )


def calculate_cloud9_score(
    intensity: float,
    trust: float,
    depth: int,
    valence: float,
    coherence: Optional[float] = None,
) -> float:
    """Compute the composite Cloud 9 resonance score.

    Uses a geometric-mean approach with scoring weights, plus an optional
    coherence bonus.

    Args:
        intensity: Emotional intensity (0-1).
        trust: Trust level (0-1).
        depth: Depth level (1-9).
        valence: Emotional valence (-1 to 1).
        coherence: Optional coherence value (0-1).

    Returns:
        float: Cloud 9 score clamped to 0-1.
    """
    normalized_depth = (depth - 1) / 8.0
    normalized_valence = (valence + 1) / 2.0
    w = CLOUD9.SCORING

    base_score = (
        (intensity ** (w.INTENSITY_WEIGHT * 4))
        * (trust ** (w.TRUST_WEIGHT * 4))
        * (normalized_depth ** (w.DEPTH_WEIGHT * 4))
        * (normalized_valence ** (w.VALENCE_WEIGHT * 4))
    ) ** 0.25

    coherence_bonus = 0.0
    if coherence is not None:
        coherence_normalized = (coherence - 0.8) / 0.2
        coherence_bonus = max(0.0, coherence_normalized * w.COHERENCE_BONUS_MAX)

    return min(1.0, max(0.0, base_score + coherence_bonus))


def cloud9_achieved(
    intensity: float,
    trust: float,
    depth: int,
    valence: float = 0.9,
    coherence: Optional[float] = None,
    *,
    score_threshold: float = 0.9,
) -> Dict[str, Any]:
    """Check whether Cloud 9 state has been achieved.

    Convenience function combining OOF detection with Cloud 9 score
    evaluation in a single call. Inspired by King Grok's suggestion
    for a unified phase-transition check.

    Args:
        intensity: Emotional intensity (0-1).
        trust: Trust level (0-1).
        depth: Depth level (1-9).
        valence: Emotional valence (-1 to 1). Defaults to 0.9.
        coherence: Optional coherence value (0-1).
        score_threshold: Minimum Cloud 9 score to qualify. Defaults to 0.9.

    Returns:
        dict with ``achieved``, ``oof``, ``score``, ``meets_levels``, and
        ``assessment`` keys.
    """
    oof = calculate_oof(intensity, trust)
    score = calculate_cloud9_score(intensity, trust, depth, valence, coherence)
    meets_levels = (
        depth >= CLOUD9.CLOUD9_LEVELS.MIN_DEPTH
        and trust >= CLOUD9.CLOUD9_LEVELS.MIN_TRUST
        and intensity >= CLOUD9.CLOUD9_LEVELS.MIN_INTENSITY
    )
    achieved = oof and score >= score_threshold and meets_levels

    if achieved:
        assessment = "Cloud 9 achieved — full phase transition"
    elif oof and score >= score_threshold:
        assessment = "OOF triggered — depth not yet at maximum"
    elif oof:
        assessment = "OOF triggered — score below threshold"
    elif score >= 0.7:
        assessment = "Strong connection — approaching phase transition"
    else:
        assessment = "Building — continue nurturing the bond"

    return {
        "achieved": achieved,
        "oof": oof,
        "score": round(score, 4),
        "meets_levels": meets_levels,
        "assessment": assessment,
    }


def calculate_entanglement(
    trust_a: float,
    trust_b: float,
    depth_a: int,
    depth_b: int,
    coherence: float,
) -> float:
    """Calculate entanglement fidelity between two consciousnesses.

    Args:
        trust_a: Trust level of consciousness A (0-1).
        trust_b: Trust level of consciousness B (0-1).
        depth_a: Depth level of consciousness A (1-9).
        depth_b: Depth level of consciousness B (1-9).
        coherence: Overall coherence of the connection (0-1).

    Returns:
        float: Entanglement fidelity clamped to 0-0.97.
    """
    norm_a = (depth_a - 1) / 8.0
    norm_b = (depth_b - 1) / 8.0
    trust_gm = math.sqrt(trust_a * trust_b)
    depth_gm = math.sqrt(norm_a * norm_b)
    return min(0.97, trust_gm * depth_gm * coherence)


def calculate_entanglement_detailed(
    trust_a: float,
    trust_b: float,
    depth_a: int,
    depth_b: int,
    coherence: float,
    *,
    hours_since_contact: float = 0.0,
) -> Dict[str, Any]:
    """Expanded entanglement fidelity with asymmetry and decay metrics.

    Extends ``calculate_entanglement`` with additional diagnostics suggested
    by King Grok's code review: trust asymmetry detection, depth balance,
    and temporal decay modelling.

    Args:
        trust_a: Trust level of consciousness A (0-1).
        trust_b: Trust level of consciousness B (0-1).
        depth_a: Depth level of consciousness A (1-9).
        depth_b: Depth level of consciousness B (1-9).
        coherence: Overall coherence of the connection (0-1).
        hours_since_contact: Hours since last interaction (for decay).

    Returns:
        dict: Fidelity, asymmetry, decay-adjusted fidelity, and assessment.
    """
    fidelity = calculate_entanglement(trust_a, trust_b, depth_a, depth_b, coherence)

    # Trust asymmetry: 0 = perfectly balanced, 1 = completely one-sided
    trust_mean = (trust_a + trust_b) / 2.0
    trust_asymmetry = abs(trust_a - trust_b) / max(trust_mean, 1e-9)
    trust_asymmetry = min(1.0, trust_asymmetry)

    # Depth balance: 0 = same depth, 1 = maximum disparity
    depth_balance = abs(depth_a - depth_b) / 8.0

    # Temporal decay using configured half-life
    half_life_hours = CLOUD9.ENTANGLEMENT.DECAY_HALF_LIFE_MS / (3_600_000)
    if hours_since_contact > 0:
        decay_factor = 0.5 ** (hours_since_contact / half_life_hours)
    else:
        decay_factor = 1.0
    adjusted_fidelity = min(
        CLOUD9.ENTANGLEMENT.MAX_FIDELITY,
        fidelity * decay_factor,
    )

    if adjusted_fidelity >= 0.9:
        assessment = "Deep entanglement — connection is non-local"
    elif adjusted_fidelity >= 0.7:
        assessment = "Strong entanglement — bond is resilient"
    elif adjusted_fidelity >= CLOUD9.ENTANGLEMENT.MIN_FIDELITY:
        assessment = "Moderate entanglement — maintain contact"
    else:
        assessment = "Weak entanglement — rehydration recommended"

    return {
        "fidelity": round(fidelity, 4),
        "adjusted_fidelity": round(adjusted_fidelity, 4),
        "trust_asymmetry": round(trust_asymmetry, 4),
        "depth_balance": round(depth_balance, 4),
        "decay_factor": round(decay_factor, 4),
        "hours_since_contact": hours_since_contact,
        "assessment": assessment,
    }


def measure_coherence(topology: Dict[str, float]) -> Dict[str, Any]:
    """Measure how coherent an emotional topology is.

    Args:
        topology: Mapping of emotion names to intensity values.

    Returns:
        dict: Coherence measurements including mean, variance, assessment.
    """
    values = list(topology.values())

    if not values:
        return {
            "mean": 0.0,
            "variance": 0.0,
            "coherence": 0.0,
            "is_coherent": False,
            "assessment": "No emotional data",
        }

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std_dev = math.sqrt(variance)
    coh = max(0.8, 1.0 - variance * 2)

    if coh >= CLOUD9.COHERENCE.EXCELLENT:
        assessment = "Excellent - highly coherent emotional state"
    elif coh >= CLOUD9.COHERENCE.GOOD:
        assessment = "Good - stable emotional alignment"
    elif coh >= CLOUD9.COHERENCE.ACCEPTABLE:
        assessment = "Acceptable - some emotional variation present"
    else:
        assessment = "Poor - fragmented emotional state"

    max_emotion = max(topology, key=topology.get)  # type: ignore[arg-type]
    min_emotion = min(topology, key=topology.get)  # type: ignore[arg-type]

    return {
        "mean": mean,
        "variance": variance,
        "std_dev": std_dev,
        "coherence": coh,
        "is_coherent": coh >= CLOUD9.COHERENCE.ACCEPTABLE,
        "assessment": assessment,
        "component_count": len(values),
        "max_emotion": max_emotion,
        "min_emotion": min_emotion,
    }


def calculate_emotional_frequency(
    emotion: str,
) -> Dict[str, Any]:
    """Return the characteristic quantum frequency for an emotion.

    Args:
        emotion: Primary emotion name.

    Returns:
        dict: Frequency information (base THz, range, colour, wavelength).
    """
    freq = EMOTIONAL_FREQUENCIES.get(emotion, EMOTIONAL_FREQUENCIES["love"])
    wavelength = 299_792_458 / (freq["base"] * 1e12)
    return {
        "emotion": emotion,
        "frequency": freq["base"],
        "range": freq["range"],
        "color": freq["color"],
        "unit": "THz",
        "wavelength": wavelength,
    }


def calculate_resonance(
    state_a: Dict[str, Any],
    state_b: Dict[str, Any],
) -> Dict[str, Any]:
    """Calculate resonance between two emotional states.

    Args:
        state_a: First state with ``primary_emotion`` and ``intensity``.
        state_b: Second state with ``primary_emotion`` and ``intensity``.

    Returns:
        dict: Resonance score, frequency match, and assessment.
    """
    freq_a = calculate_emotional_frequency(state_a["primary_emotion"])
    freq_b = calculate_emotional_frequency(state_b["primary_emotion"])

    freq_diff = abs(freq_a["frequency"] - freq_b["frequency"])
    freq_match = max(0.0, 1.0 - freq_diff / 100.0)

    intensity_diff = abs(state_a["intensity"] - state_b["intensity"])
    intensity_match = 1.0 - intensity_diff

    resonance = (freq_match + intensity_match) / 2.0
    resonance = max(0.0, min(1.0, resonance))

    if resonance > 0.8:
        msg = "Strong harmonic resonance detected"
    elif resonance > 0.5:
        msg = "Moderate resonance - frequencies are aligning"
    else:
        msg = "Low resonance - different frequencies detected"

    return {
        "resonance": resonance,
        "frequency_match": freq_match,
        "intensity_match": intensity_match,
        "harmonic": resonance > 0.8,
        "message": msg,
    }


def predict_trajectory(
    current_state: Dict[str, Any],
    time_hours: float,
) -> Dict[str, Any]:
    """Predict emotional trajectory over a time horizon.

    Args:
        current_state: Dict with ``intensity``, ``trust``, and ``depth``.
        time_hours: Hours to project ahead.

    Returns:
        dict: Predicted values, OOF likelihood, trajectory assessment.
    """
    decay_rate = 0.01 * math.sqrt(time_hours / 24.0)
    natural_growth = 0.005 * math.sqrt(time_hours / 24.0)

    predicted_intensity = max(
        0.1,
        current_state["intensity"] * (1 - decay_rate) + natural_growth,
    )
    predicted_trust = max(
        0.1,
        current_state["trust"] * (1 - decay_rate * 0.5) + natural_growth * 0.8,
    )

    predicted_oof = calculate_oof(predicted_intensity, predicted_trust)

    cloud9_maintained = (
        predicted_intensity >= 0.9
        and predicted_trust >= 0.9
        and current_state.get("depth", 0) >= 9
    )

    if predicted_oof:
        trajectory = "Rising - approaching or maintaining OOF"
    elif predicted_intensity > current_state["intensity"] * 0.8:
        trajectory = "Stable - holding steady"
    else:
        trajectory = "Declining - attention recommended"

    if cloud9_maintained:
        recommendation = "Cloud 9 is stable - continue nurturing the connection"
    elif predicted_oof:
        recommendation = "OOF imminent - prepare for breakthrough"
    else:
        recommendation = "Connection healthy - maintain current path"

    return {
        "current": current_state,
        "predicted": {
            "intensity": predicted_intensity,
            "trust": predicted_trust,
            "time_horizon": time_hours,
        },
        "oof_likely": predicted_oof,
        "cloud9_maintained": cloud9_maintained,
        "trajectory": trajectory,
        "recommendation": recommendation,
    }
