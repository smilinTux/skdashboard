"""Read-only dashboard assistant boundary."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .assistant_client import AssistantClientError, AssistantScope, get_client

logger = logging.getLogger("skcapstone.dashboard.assistant")

MAX_NOW_FACTS = 20
MAX_NOW_CONTEXT_CHARS = 24_000


class NowBriefReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=128)
    observed_at: str | None = Field(default=None, max_length=64)
    freshness: str = Field(min_length=1, max_length=32)


class NowBriefInsight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str = Field(min_length=1, max_length=240)
    sources: list[NowBriefReference] = Field(min_length=1, max_length=2)
    uncertainty: str = Field(min_length=1, max_length=240)


class NowBriefNextStep(NowBriefInsight):
    rank: int = Field(ge=1, le=10)
    proposal: str = Field(min_length=1, max_length=240)


class NowOperatorBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["skdashboard.now-operator-brief.v1"]
    status: Literal["proposal", "abstained"]
    generated_at: str
    conditions: list[NowBriefInsight] = Field(default_factory=list, max_length=1)
    risks: list[NowBriefInsight] = Field(default_factory=list, max_length=1)
    anomalies: list[NowBriefInsight] = Field(default_factory=list, max_length=1)
    next_steps: list[NowBriefNextStep] = Field(default_factory=list, max_length=3)
    abstention: str | None = Field(default=None, max_length=240)


def now_operator_brief(overview: dict, actor: str = "operator") -> dict:
    """Return a typed proposal grounded only in bounded aggregate NOW facts."""
    facts = []
    for item in overview.get("items", [])[:MAX_NOW_FACTS]:
        source_id = item.get("adapter_id") or item.get("projection_type")
        if not isinstance(source_id, str) or not source_id:
            continue
        facts.append(
            {
                "source_id": source_id[:128],
                "observed_at": item.get("observed_at"),
                "freshness": item.get("truth_state", "unknown"),
                "silo": item.get("silo"),
                "signal": item.get("signal"),
                "aggregate": item.get("aggregate"),
                "coverage": item.get("coverage"),
                "errors": [str(value)[:256] for value in item.get("errors", [])[:3]],
            }
        )
    context = json.dumps(
        {"scope": overview.get("scope", {}), "facts": facts},
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(context) > MAX_NOW_CONTEXT_CHARS:
        raise ValueError("NOW aggregate context is too large")
    messages = [
        {
            "role": "system",
            "content": (
                "Return only JSON matching skdashboard.now-operator-brief.v1. Analyze only "
                "the supplied aggregate facts. Include source_id, observed_at, freshness, and "
                "uncertainty for every insight. Rank next steps as read-only proposals. Abstain "
                "when evidence is insufficient. Return no more than one condition, one risk, "
                "one anomaly, and three next steps. Be concise. Never emit commands, tools, "
                "or actions."
            ),
        },
        {"role": "user", "content": f"CURRENT NOW FACTS:\n{context}"},
    ]
    proposal = NowOperatorBrief.model_validate_json(
        get_client().chat(
            messages,
            actor=actor,
            card_id="4e9bdbe3",
            require_retrieval_traces=False,
            max_tokens=1200,
            response_schema=NowOperatorBrief.model_json_schema(),
        )
    )
    allowed_sources = {fact["source_id"] for fact in facts}
    references = [
        ref
        for insight in [
            *proposal.conditions,
            *proposal.risks,
            *proposal.anomalies,
            *proposal.next_steps,
        ]
        for ref in insight.sources
    ]
    if any(ref.source_id not in allowed_sources for ref in references):
        raise ValueError("NOW brief cited an unauthorized source")
    if [step.rank for step in proposal.next_steps] != list(
        range(1, len(proposal.next_steps) + 1)
    ):
        raise ValueError("NOW brief next steps are not ranked")
    return proposal.model_dump(mode="json")


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
