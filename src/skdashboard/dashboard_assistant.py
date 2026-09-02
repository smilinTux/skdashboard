"""Read-only dashboard assistant over bounded aggregate observations.

The assistant receives only a compact board and ITIL aggregate snapshot. It
routes through the shared SKGateway abstraction and has no action parser,
command dispatcher, mutation capability, tools, or direct provider access.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("skcapstone.dashboard.assistant")

# ---------------------------------------------------------------------------
# Analytics (canned reports the model narrates)
# ---------------------------------------------------------------------------


def board_summary(home: Path) -> dict:
    from skcoord.card import KanbanBoard

    kb = KanbanBoard(home)
    cards = kb.cards()
    from collections import Counter

    by_col = Counter(c.status.value for c in cards)
    by_lane = Counter(c.swimlane for c in cards)
    return {
        "active": len(cards),
        "by_column": dict(by_col),
        "by_lane": dict(by_lane),
        "wip": kb.wip_report(),
    }


def build_context(home: Path) -> str:
    """Serialize only aggregate board and ITIL observations for the model."""
    from . import dashboard_itil as di

    board = board_summary(home)
    overview = di.get_overview(home)
    kpis = overview.get("kpis", {}) if isinstance(overview, dict) else {}
    aggregate = {
        "kanban": {
            "active": board["active"],
            "by_column": board["by_column"],
            "by_lane": board["by_lane"],
            "wip": board["wip"],
        },
        "itil": {
            "kpis": kpis if isinstance(kpis, dict) else {},
            "by_severity": overview.get("by_severity", {})
            if isinstance(overview, dict)
            else {},
        },
    }
    return json.dumps(aggregate, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are the read-only SKDashboard assistant. Answer the operator using ONLY "
    "the bounded aggregate snapshot. Be concise and concrete. Never request or "
    "emit a command, action, tool call, mutation, credential, capability, or "
    "provider endpoint. If asked to act, state that this surface is read only."
)


def stream_answer(home: Path, prompt: str, actor: str = "operator"):
    """Yield read-only assistant SSE frames through the approved gateway facade."""
    from .assistant_client import AssistantClientError, get_client

    context = build_context(home)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"AGGREGATE SNAPSHOT:\n{context}\n\nQUESTION: {prompt}"},
    ]
    got_any = False
    try:
        for token in get_client().chat_stream(messages, actor=actor):
            got_any = True
            yield _sse("token", {"text": token})
    except AssistantClientError as exc:
        logger.warning(
            "read-only assistant unavailable",
            extra={"actor": actor[:128], "reason": exc.reason},
        )
    if not got_any:
        yield _sse("token", {"text": "(read-only assistant is unavailable right now)"})
    yield _sse("done", {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
