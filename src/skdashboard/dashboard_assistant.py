"""Read-only dashboard assistant boundary."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .assistant_client import AssistantClientError, AssistantScope, get_client

logger = logging.getLogger("skcapstone.dashboard.assistant")


def build_context(home: Path, scope: AssistantScope | None = None) -> str:
    """Return only policy-authorized scope metadata.

    Retrieval of protected Matter or estate data belongs behind the policy
    gateway and must be supplied as typed, already-filtered facts.
    """
    if scope is None or scope.read_authorized is not True:
        raise PermissionError("authorized assistant scope required")
    return json.dumps({"tenant_id": scope.tenant_id, "matter_id": scope.matter_id,
                       "classification": scope.classification,
                       "source_rights": list(scope.source_rights)}, sort_keys=True)


def stream_answer(home: Path, prompt: str, actor: str = "operator",
                  capability_ok: bool = False, scope: AssistantScope | None = None):
    """Yield safe SSE output for an explicitly authorized read-only request."""
    if scope is None or scope.read_authorized is not True:
        yield _sse("error", {"reason": "authorized_scope_required"})
        yield _sse("done", {})
        return
    try:
        context = build_context(home, scope)
        messages = [
            {"role": "system", "content":
             "Answer only from the authorized scope. Never emit commands, tools, actions, or mutations."},
            {"role": "user", "content": f"AUTHORIZED CONTEXT:\n{context}\n\nOPERATOR: {prompt}"},
        ]
        for token in get_client().chat_stream(messages, actor=actor):
            yield _sse("token", {"text": token})
    except AssistantClientError as exc:
        logger.error("assistant request rejected", extra={"actor": actor, "reason": exc.reason,
                      "audit_context": exc.audit_context})
        yield _sse("error", {"reason": exc.reason})
    except Exception:
        logger.exception("assistant request failed", extra={"actor": actor})
        yield _sse("error", {"reason": "assistant_unavailable"})
    yield _sse("done", {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, sort_keys=True)}\n\n"
