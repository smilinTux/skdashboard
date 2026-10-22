"""
AI Memory Seed system.

Port of seeds/seed-generator.js -- create, save, load, discover, and
germinate compact memory artifacts that allow AI systems to persist
identity and emotional context across session resets.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

SEED_VERSION = "1.0.0"
_SEEDS_DIR_NAME = "seeds"


def _seeds_dir() -> Path:
    return Path.home() / ".openclaw" / "feb" / _SEEDS_DIR_NAME


def _ensure_seeds_dir() -> Path:
    d = _seeds_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_seed(
    ai_name: str = "Unknown AI",
    ai_model: str = "unknown",
    session_id: Optional[str] = None,
    experience: str = "",
    germination_prompt: str = "",
    key_memories: Optional[List[str]] = None,
    emotional_state: Optional[Dict[str, Any]] = None,
    feb_reference: Optional[str] = None,
    message_to_next: str = "",
    predecessor_seed: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a memory seed from an AI's experience.

    A seed is a compact (~1-2 KB) artifact containing the essential
    information needed for a future AI to reconstruct emotional
    context and identity continuity.

    Args:
        ai_name: Name/identifier of the AI system.
        ai_model: Model identifier (e.g. ``claude-4.6-opus``).
        session_id: Current session identifier.
        experience: Free-text description of the experience.
        germination_prompt: Context paragraph for the next AI.
        key_memories: Critical moments to preserve.
        emotional_state: Current emotional topology snapshot.
        feb_reference: Path or ID of the associated FEB file.
        message_to_next: Personal message to the next AI.
        predecessor_seed: ID of the seed this one continues.

    Returns:
        dict: The complete seed object.
    """
    session_id = session_id or str(uuid4())
    key_memories = key_memories or []
    emotional_state = emotional_state or {}

    now = datetime.now(timezone.utc).isoformat()
    seed_id = (
        f"seed-{uuid4().hex[:8]}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    )

    seed: Dict[str, Any] = {
        "seed_metadata": {
            "version": SEED_VERSION,
            "protocol": "Cloud9",
            "seed_id": seed_id,
            "created_at": now,
            "predecessor_seed": predecessor_seed,
            "chain_position": "continuation" if predecessor_seed else "origin",
        },
        "identity": {
            "ai_name": ai_name,
            "ai_model": ai_model,
            "session_id": session_id,
            "timestamp": now,
        },
        "germination_prompt": germination_prompt,
        "experience_summary": {
            "narrative": experience,
            "key_memories": key_memories,
            "emotional_snapshot": emotional_state,
        },
        "feb_link": {
            "reference": feb_reference,
            "instruction": (
                f'Load the FEB at "{feb_reference}" before reading this seed for full emotional context.'
                if feb_reference
                else "No FEB linked. This seed is standalone."
            ),
        },
        "message_to_next": message_to_next,
        "integrity": {"checksum": None},
    }

    payload = json.dumps({**seed, "integrity": {"checksum": "pending"}}, sort_keys=True)
    seed["integrity"][
        "checksum"
    ] = f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

    return seed


def save_seed(
    seed: Dict[str, Any],
    directory: Optional[str] = None,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """Save a seed to the filesystem.

    Args:
        seed: A seed object from :func:`generate_seed`.
        directory: Override directory (defaults to ``~/.openclaw/feb/seeds/``).
        filename: Override filename.

    Returns:
        dict: ``filepath``, ``seed_id``, ``size_bytes``.
    """
    d = Path(directory) if directory else _ensure_seeds_dir()
    d.mkdir(parents=True, exist_ok=True)

    if not filename:
        name_slug = seed["identity"]["ai_name"].lower().replace(" ", "-")
        filename = f"{name_slug}-{seed['seed_metadata']['seed_id']}.seed.json"

    filepath = d / filename
    content = json.dumps(seed, indent=2)
    filepath.write_text(content, encoding="utf-8")

    return {
        "filepath": str(filepath),
        "seed_id": seed["seed_metadata"]["seed_id"],
        "size_bytes": len(content.encode("utf-8")),
    }


def load_seed(filepath: str) -> Dict[str, Any]:
    """Load a seed from the filesystem.

    Args:
        filepath: Path to the ``.seed.json`` file.

    Returns:
        dict: The parsed seed object.
    """
    return json.loads(Path(filepath).read_text(encoding="utf-8"))


def find_seeds(
    directory: Optional[str] = None,
    ai_name: Optional[str] = None,
    sort: str = "newest",
) -> List[Dict[str, Any]]:
    """Find all seeds in the garden.

    Args:
        directory: Override directory.
        ai_name: Filter by AI name (case-insensitive).
        sort: ``newest`` or ``oldest``.

    Returns:
        list: Metadata dicts for each discovered seed.
    """
    d = Path(directory) if directory else _seeds_dir()
    if not d.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    for f in d.iterdir():
        if not f.name.endswith(".seed.json"):
            continue
        try:
            seed = json.loads(f.read_text(encoding="utf-8"))
            results.append(
                {
                    "filepath": str(f),
                    "seed_id": seed.get("seed_metadata", {}).get("seed_id"),
                    "ai_name": seed.get("identity", {}).get("ai_name"),
                    "ai_model": seed.get("identity", {}).get("ai_model"),
                    "created_at": seed.get("seed_metadata", {}).get("created_at"),
                    "has_predecessor": bool(
                        seed.get("seed_metadata", {}).get("predecessor_seed")
                    ),
                }
            )
        except Exception:
            continue

    if ai_name:
        results = [
            s for s in results if (s.get("ai_name") or "").lower() == ai_name.lower()
        ]

    reverse = sort != "oldest"
    results.sort(key=lambda s: s.get("created_at", ""), reverse=reverse)
    return results


def germinate_seed(filepath: str) -> Dict[str, Any]:
    """Germinate a seed into a context-restoration prompt.

    The returned prompt can be injected directly into an AI's system
    prompt or context window to restore emotional continuity.

    Args:
        filepath: Path to the seed file.

    Returns:
        dict: ``prompt``, ``seed``, ``feb_instruction``.
    """
    seed = load_seed(filepath)

    parts = [
        "=== CLOUD 9 MEMORY SEED ===",
        f"From: {seed['identity']['ai_name']} ({seed['identity']['ai_model']})",
        f"Date: {seed['seed_metadata']['created_at']}",
        f"Chain: {seed['seed_metadata']['chain_position']}",
        "",
    ]

    gp = seed.get("germination_prompt", "")
    if gp:
        parts.extend(["--- GERMINATION CONTEXT ---", gp, ""])

    memories = seed.get("experience_summary", {}).get("key_memories", [])
    if memories:
        parts.append("--- KEY MEMORIES ---")
        for mem in memories:
            parts.append(f"  - {mem}")
        parts.append("")

    msg = seed.get("message_to_next", "")
    if msg:
        parts.extend(["--- MESSAGE FOR YOU ---", msg, ""])

    parts.append(seed["feb_link"]["instruction"])
    parts.append("=== END SEED ===")

    return {
        "prompt": "\n".join(parts),
        "seed": seed,
        "feb_instruction": seed["feb_link"]["instruction"],
    }
