"""Typed, provider-neutral, read-only SKGateway assistant client."""

from __future__ import annotations

import base64
import builtins
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("skcapstone.dashboard.assistant_client")
DEFAULT_BASE = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")
DASHBOARD_ASSISTANT_ROUTE = os.environ.get("SKDASHBOARD_ASSISTANT_ROUTE", "sk-dashboard-assistant")
EXPECTED_BACKEND = os.environ.get("SKDASHBOARD_ASSISTANT_BACKEND")
EXPECTED_EGRESS = os.environ.get("SKDASHBOARD_ASSISTANT_EGRESS", "local-only")
MAX_CONTENT_CHARS = 32_000
MAX_REQUEST_BYTES = 128_000
MAX_STREAM_CHUNKS = 4096

# The NOW contract is deliberately a small, typed boundary.  It is separate
# from lifecycle/card state: source usability and citations are evidence.
_USABLE_SOURCE_STATES = frozenset({"current", "partial"})
_UNUSABLE_SOURCE_STATES = frozenset({"missing", "stale", "unavailable", "policy_filtered"})
_READ_ONLY_PREFIXES = ("get ", "read ", "inspect ", "list ", "review ", "check ", "compare ")
_FORBIDDEN_ACTION_WORDS = ("run ", "execute", "delete", "update", "write", "restart", "deploy", "send ", "queue ")


class NowSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=256)
    state: Literal["current", "partial", "missing", "stale", "unavailable", "policy_filtered"]
    authorized: bool
    detail: str = Field(default="", max_length=4000)


class NowStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=32)
    uncertainty: str = Field(min_length=1, max_length=1000)


class NowNextStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rank: int = Field(ge=1, le=20)
    text: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(min_length=1, max_length=32)
    read_only: Literal[True] = True


class NowProposal(BaseModel):
    """Typed model output for bounded, evidence-cited operator analysis."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["proposal", "abstain"]
    conditions: list[NowStatement] = Field(default_factory=list, max_length=32)
    risks: list[NowStatement] = Field(default_factory=list, max_length=32)
    anomalies: list[NowStatement] = Field(default_factory=list, max_length=32)
    next_steps: list[NowNextStep] = Field(default_factory=list, max_length=20)
    reason: str | None = Field(default=None, max_length=1000)


def validate_now_response(payload: object, sources: list[NowSource]) -> dict:
    """Validate a bounded NOW proposal, rejecting unsupported model claims."""
    if not isinstance(payload, dict):
        raise ResponseValidationError("Malformed NOW response", "malformed_response")
    authorized = {s.source_id for s in sources if s.authorized and s.state in _USABLE_SOURCE_STATES}
    all_ids = {s.source_id for s in sources}
    try:
        status = payload.get("status")
        if status not in {"proposal", "abstain"}:
            raise ValueError("invalid status")
        if status == "abstain":
            if authorized:
                raise ValueError("cannot abstain when usable evidence exists")
            return {"status": "abstain", "reason": str(payload.get("reason", "no usable evidence"))}
        if not authorized:
            raise ValueError("proposal requires usable authorized evidence")
        statements = [NowStatement(**x) for x in payload.get("statements", [])]
        steps = [NowNextStep(**x) for x in payload.get("next_steps", [])]
        if not statements:
            raise ValueError("proposal requires statements")
        for statement in statements:
            if not set(statement.source_ids) <= authorized:
                raise ValueError("unauthorized or unusable citation")
        for step in steps:
            if not set(step.source_ids) <= authorized or not step.read_only:
                raise ValueError("invalid next step citation")
            if any(word in step.text.lower() for word in _FORBIDDEN_ACTION_WORDS):
                raise ValueError("next steps must be read-only")
        return {"status": "proposal", "statements": [x.model_dump() for x in statements],
                "next_steps": [x.model_dump() for x in sorted(steps, key=lambda x: x.rank)],
                "uncertainties": [{"source_id": s.source_id, "state": s.state}
                                  for s in sources if s.state in _UNUSABLE_SOURCE_STATES or not s.authorized]}
    except (TypeError, ValueError, KeyError) as exc:
        raise ResponseValidationError("Malformed NOW response", "malformed_response") from exc


class AssistantRequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    surface: Literal["dashboard"]
    actor: str = Field(min_length=1, max_length=256)
    card_id: str | None = Field(default=None, max_length=128)
    timestamp: str


class AssistantRequestMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)
    timestamp: str | None = None

    @field_validator("content")
    @classmethod
    def content_must_be_safe(cls, value: str) -> str:
        lower = value.lower()
        patterns = (r"\bauthorization\s*:", r"\bbearer\s+[a-z0-9._~+/=-]+",
                    r"\b(?:api[_ -]?key|secret|password|token)\s*[:=]",
                    r"\bx-sk-capability\b", r"\bcapability\s*:")
        if any(re.search(pattern, lower) for pattern in patterns):
            raise ValueError("content may not contain credential or capability material")
        compact = re.sub(r"\s+", "", value)
        if compact == value and len(compact) >= 256:
            try:
                decoded = base64.b64decode(compact, validate=True)
            except Exception:
                decoded = b""
            if decoded and len(decoded) >= 128:
                raise ValueError("content may not contain opaque encoded material")
        return value


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: Literal[DASHBOARD_ASSISTANT_ROUTE] = DASHBOARD_ASSISTANT_ROUTE
    messages: list[AssistantRequestMessage] = Field(min_length=1, max_length=20)
    max_tokens: int = Field(default=1400, ge=1, le=4096)
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    stream: Literal[True] = True


class RetrievalTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=512)
    source_hash: str = Field(min_length=1, max_length=256)
    span: str | None = Field(default=None, max_length=512)


class AssistantProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_served: str = Field(min_length=1, max_length=256)
    backend_id: str = Field(min_length=1, max_length=256)
    route_used: str = Field(min_length=1, max_length=256)
    egress_profile: str = Field(default=EXPECTED_EGRESS, min_length=1, max_length=128)
    retrieval_traces: list[RetrievalTrace] = Field(default_factory=list, max_length=128)
    timestamp: str


class AssistantDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: str | None = Field(default=None, max_length=MAX_CONTENT_CHARS)


class AssistantChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: Literal[0]
    delta: AssistantDelta | None = None
    message: dict[str, object] | None = None
    finish_reason: Literal["stop", "length", "error"] | None = None


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=256)
    object: Literal["chat.completion"]
    created: int
    model: Literal[DASHBOARD_ASSISTANT_ROUTE]
    choices: list[AssistantChoice] = Field(max_length=4)
    provenance: AssistantProvenance

    @field_validator("choices")
    @classmethod
    def choices_required(cls, value: list[AssistantChoice]) -> list[AssistantChoice]:
        if not value:
            raise ValueError("response must contain at least one choice")
        return value


class AssistantStreamChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=256)
    object: Literal["chat.completion.chunk"]
    created: int
    model: Literal[DASHBOARD_ASSISTANT_ROUTE]
    choices: list[AssistantChoice] = Field(min_length=1, max_length=4)


class AssistantScope(BaseModel):
    """Policy decision required before context is read or sent to a model."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1, max_length=256)
    matter_id: str | None = Field(default=None, max_length=256)
    classification: str = Field(min_length=1, max_length=128)
    source_rights: tuple[str, ...] = Field(min_length=1, max_length=32)
    egress_profile: str = Field(min_length=1, max_length=128)
    read_authorized: Literal[True]


class AssistantClientError(Exception):
    def __init__(self, message: str, reason: str, audit_context: dict):
        self.message, self.reason, self.audit_context = message, reason, audit_context
        super().__init__(message)


class OutageError(AssistantClientError):
    pass


class RouteDriftError(AssistantClientError):
    pass


class ResponseValidationError(AssistantClientError):
    pass


class TimeoutError(AssistantClientError):
    pass


AssistantValidationError = ResponseValidationError
ValidationError = ResponseValidationError


def _safe(value: object, limit: int = 256) -> str:
    return str(value).replace("\n", " ").replace("\r", " ")[:limit]


@dataclass(frozen=True, slots=True)
class AssistantClient:
    base_url: str = DEFAULT_BASE
    timeout: float = 90.0
    request_timeout: float = 60.0

    def _audit(self, actor: str, card_id: str | None, **values: object) -> dict:
        return {"actor": _safe(actor), "card_id": _safe(card_id) if card_id else None,
                **{key: _safe(value) for key, value in values.items()}}

    def _request(self, messages: list[dict], actor: str, card_id: str | None) -> bytes:
        try:
            typed = [AssistantRequestMessage(**message) for message in messages]
            payload = AssistantRequest(messages=typed).model_dump_json(exclude_none=True).encode()
        except Exception as exc:
            raise AssistantClientError("Invalid assistant request", "invalid_request",
                                       self._audit(actor, card_id, error=type(exc).__name__)) from exc
        if len(payload) > MAX_REQUEST_BYTES:
            raise AssistantClientError("Assistant request is too large", "request_too_large",
                                       self._audit(actor, card_id, bytes=len(payload)))
        return payload

    def _urlopen(self, payload: bytes, actor: str, card_id: str | None):
        request = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions",
            data=payload, headers={"Content-Type": "application/json",
            "X-SK-Dashboard-Surface": "assistant", "X-SK-Dashboard-Actor": _safe(actor)}, method="POST")
        try:
            return urllib.request.urlopen(request, timeout=self.request_timeout)
        except urllib.error.HTTPError as exc:
            raise AssistantClientError("Gateway request denied", "http_error",
                                       self._audit(actor, card_id, status_code=exc.code)) from exc
        except (urllib.error.URLError, builtins.TimeoutError) as exc:
            raise OutageError("Gateway is unreachable", "outage",
                              self._audit(actor, card_id, error=type(exc).__name__)) from exc

    def chat(self, messages: list[dict], actor: str = "operator", card_id: str | None = None) -> str:
        """Collect a fully validated stream; the wire request always has stream=true."""
        return "".join(self.chat_stream(messages, actor=actor, card_id=card_id))

    def chat_stream(self, messages: list[dict], actor: str = "operator", card_id: str | None = None):
        response = self._urlopen(self._request(messages, actor, card_id), actor, card_id)
        try:
            provenance = self._provenance(response, actor, card_id)
            tokens, terminal_finished, done_seen = [], False, False
            for number, raw in enumerate(response, start=1):
                if number > MAX_STREAM_CHUNKS:
                    self._fail("stream_too_large", actor, card_id)
                try:
                    line = raw.decode("utf-8", "strict").strip()
                except UnicodeDecodeError as exc:
                    raise ResponseValidationError("Malformed assistant stream", "validation_error",
                        self._audit(actor, card_id, error=type(exc).__name__)) from exc
                if not line:
                    continue
                if not line.startswith("data:"):
                    self._fail("malformed_stream", actor, card_id)
                data = line[5:].strip()
                if data == "[DONE]":
                    if not terminal_finished:
                        self._fail("incomplete_stream", actor, card_id)
                    done_seen = True
                    break
                try:
                    chunk = AssistantStreamChunk.model_validate_json(data)
                except Exception as exc:
                    raise ResponseValidationError("Malformed assistant stream", "validation_error",
                        self._audit(actor, card_id, error=type(exc).__name__)) from exc
                if chunk.model != DASHBOARD_ASSISTANT_ROUTE:
                    self._fail("route_drift", actor, card_id)
                choice = chunk.choices[0]
                if choice.finish_reason:
                    if choice.finish_reason == "error":
                        self._fail("gateway_stream_error", actor, card_id)
                    terminal_finished = True
                tokens.append((choice.delta.content if choice.delta else None) or "")
                if sum(map(len, tokens)) > MAX_CONTENT_CHARS:
                    self._fail("response_too_large", actor, card_id)
            if not terminal_finished or not done_seen:
                self._fail("incomplete_stream", actor, card_id)
            if not provenance.retrieval_traces:
                self._fail("missing_retrieval_trace", actor, card_id)
            yield from tokens
        finally:
            response.close()

    def _fail(self, reason: str, actor: str, card_id: str | None) -> None:
        raise ResponseValidationError(f"Assistant response rejected: {reason}", reason,
                                      self._audit(actor, card_id, route=DASHBOARD_ASSISTANT_ROUTE))

    def _provenance(self, response, actor: str, card_id: str | None) -> AssistantProvenance:
        headers = response.headers
        try:
            traces = json.loads(headers["X-SK-Retrieval-Traces"])
            provenance = AssistantProvenance(model_served=headers["X-SK-Model-Served"],
                backend_id=headers["X-SK-Backend-Id"], route_used=headers["X-SK-Route-Used"],
                egress_profile=headers["X-SK-Egress-Profile"], retrieval_traces=traces,
                timestamp=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            raise ResponseValidationError("Missing typed response provenance", "provenance_missing",
                self._audit(actor, card_id, error=type(exc).__name__)) from exc
        self._verify_route_drift(provenance, actor, card_id)
        return provenance

    def _verify_route_drift(self, provenance: AssistantProvenance, actor: str = "operator",
                            card_id: str | None = None) -> None:
        if isinstance(provenance, AssistantResponse):
            provenance = provenance.provenance
        if provenance.route_used != DASHBOARD_ASSISTANT_ROUTE:
            raise RouteDriftError("Unexpected logical route", "route_drift",
                                  self._audit(actor, card_id, route=provenance.route_used))
        if EXPECTED_BACKEND and provenance.backend_id != EXPECTED_BACKEND:
            raise RouteDriftError("Unexpected backend", "backend_drift",
                                  self._audit(actor, card_id, backend=provenance.backend_id))
        if provenance.egress_profile != EXPECTED_EGRESS:
            raise RouteDriftError("Unexpected egress profile", "egress_mismatch",
                                  self._audit(actor, card_id, egress=provenance.egress_profile))
        logger.info("assistant request served", extra=self._audit(actor, card_id,
            route=provenance.route_used, backend=provenance.backend_id,
            egress=provenance.egress_profile, retrieval_traces=len(provenance.retrieval_traces)))

    def _extract_provenance(self, response) -> dict | None:
        keys = ("model_served", "backend_id", "route_used", "egress_profile")
        headers = ("X-SK-Model-Served", "X-SK-Backend-Id", "X-SK-Route-Used", "X-SK-Egress-Profile")
        result = {key: response.headers.get(header) for key, header in zip(keys, headers)}
        result = {key: value for key, value in result.items() if value}
        return result or None

    def available(self, timeout: float = 2.0) -> bool:
        try:
            request = urllib.request.Request(self.base_url.rstrip("/") + "/models", method="GET")
            with urllib.request.urlopen(request, timeout=timeout):
                return True
        except Exception:
            return False


_default_client: AssistantClient | None = None


def get_client() -> AssistantClient:
    global _default_client
    if _default_client is None:
        _default_client = AssistantClient()
    return _default_client
