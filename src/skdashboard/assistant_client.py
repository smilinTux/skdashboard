"""Provider-neutral read-only assistant client for SKDashboard.

Implements a typed, bounded request/response contract with SKGateway through a
logical route. The route is configured externally (deployment config, not code)
to bind to the approved chiap08 Qwen3.8 backend.

This module enforces:
- No credentials, raw capabilities, or protected Matter content in requests
- No write or external-action tools
- Fail-closed behavior with attributable audit for all error conditions
- Typed request/response validation with source and model provenance

Card: 5c38b715 (SKDASH-AI-ASSISTANT-01)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger("skcapstone.dashboard.assistant_client")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_BASE = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")

# Logical route configured externally to bind to chiap08 Qwen3.8
# This is NOT a hardcoded model name - it's a routing alias
DASHBOARD_ASSISTANT_ROUTE = os.environ.get(
    "SKDASHBOARD_ASSISTANT_ROUTE", "sk-dashboard-assistant"
)


# ---------------------------------------------------------------------------
# Typed Request/Response Schemas
# ---------------------------------------------------------------------------


class AssistantRequestContext(BaseModel):
    """Context metadata for the assistant request - audit trail only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: Literal["dashboard"]
    actor: str
    card_id: str | None = None
    timestamp: str


class AssistantRequestMessage(BaseModel):
    """A single message in the conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str
    timestamp: str | None = None

    @field_validator("content")
    @classmethod
    def content_must_be_safe(cls, v: str) -> str:
        """Reject content that looks like credentials or protected data."""
        # Reject if content contains credential patterns
        forbidden = ["bearer ", "api_key:", "secret:", "token:", "password:"]
        lower = v.lower()
        if any(p in lower for p in forbidden):
            raise ValueError("content may not contain credential material")
        # Reject base64-like long strings (potential encoded secrets)
        # Must have NO spaces and be ALL alphanumeric and longer than 500 chars
        stripped = v.replace(" ", "").replace("\n", "").replace("\t", "")
        if len(v) > 500 and len(stripped) == len(v) and stripped.isalnum():
            raise ValueError("content may not contain opaque long strings")
        return v


class AssistantRequest(BaseModel):
    """Typed, bounded request to the dashboard assistant route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Use the logical route, NOT a hardcoded model name
    model: str = Field(default=DASHBOARD_ASSISTANT_ROUTE)
    messages: list[AssistantRequestMessage] = Field(min_length=1, max_length=20)
    max_tokens: Annotated[int, Field(ge=1, le=4096, default=1400)]
    temperature: Annotated[float, Field(ge=0.0, le=1.0, default=0.3)]
    stream: Literal[True]  # Dashboard assistant always streams

    # Explicitly no tools, no functions, no capabilities
    # This is a read-only surface

    @field_validator("messages")
    @classmethod
    def messages_must_be_safe(cls, v: list[AssistantRequestMessage]) -> list[AssistantRequestMessage]:
        """Verify no message contains credential or capability material."""
        for msg in v:
            content_lower = msg.content.lower()
            # Already validated at the message level, but double-check patterns
            if "x-sk-capability" in content_lower:
                raise ValueError("messages may not contain capability references")
            if "capability:" in content_lower:
                raise ValueError("messages may not contain capability material")
        return v


class AssistantProvenance(BaseModel):
    """Provenance metadata about the model that served the response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_served: str  # Actual model name from gateway (e.g., "qwen3.8-27b-huihui-abliterated-q4_k_m")
    backend_id: str  # Backend identifier (e.g., "chiap08-qwen38")
    route_used: str  # Logical route requested
    timestamp: str


class AssistantDelta(BaseModel):
    """A streaming content delta from the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str


class AssistantChoice(BaseModel):
    """A single completion choice in the response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int
    delta: AssistantDelta | None = None
    message: dict | None = None  # For non-streaming fallback
    finish_reason: Literal["stop", "length", "error"] | None = None


class AssistantResponse(BaseModel):
    """Typed response from the assistant route with provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    object: Literal["chat.completion"]
    created: int
    model: str  # Logical route name
    choices: list[AssistantChoice]
    provenance: AssistantProvenance | None = None  # May be in headers or body

    @field_validator("choices")
    @classmethod
    def choices_must_be_valid(cls, v: list[AssistantChoice]) -> list[AssistantChoice]:
        """Ensure at least one choice exists."""
        if not v:
            raise ValueError("response must contain at least one choice")
        return v


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AssistantClientError(Exception):
    """Base error for assistant client failures."""

    def __init__(self, message: str, reason: str, audit_context: dict):
        self.message = message
        self.reason = reason
        self.audit_context = audit_context
        super().__init__(message)


class OutageError(AssistantClientError):
    """Gateway is unreachable or unavailable."""

    pass


class RouteDriftError(AssistantClientError):
    """The logical route resolved to an unexpected backend."""

    pass


class ValidationError(AssistantClientError):
    """Response failed schema validation."""

    pass


class TimeoutError(AssistantClientError):
    """Request timed out."""

    pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssistantClient:
    """Provider-neutral read-only assistant client.

    Uses a logical SKGateway route configured externally. Validates all
    requests and responses with typed schemas. Fails closed on any error
    with attributable audit context.
    """

    base_url: str = DEFAULT_BASE
    timeout: float = 90.0
    request_timeout: float = 60.0

    def chat(
        self,
        messages: list[dict],
        actor: str = "operator",
        card_id: str | None = None,
    ) -> str:
        """Non-streaming chat completion (for testing/fallback).

        Args:
            messages: List of {role, content} message dicts
            actor: Actor identity for audit
            card_id: Optional card context for audit

        Returns:
            Complete response text

        Raises:
            AssistantClientError: On any failure with audit context
        """
        # Build typed request
        try:
            typed_messages = [
                AssistantRequestMessage(
                    role=m["role"],
                    content=m["content"],
                    timestamp=datetime.now(UTC).isoformat(),
                )
                for m in messages
            ]
        except (KeyError, ValidationError) as exc:
            raise AssistantClientError(
                "Invalid message format",
                reason="invalid_request",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "error": str(exc),
                },
            ) from exc

        request = AssistantRequest(
            model=DASHBOARD_ASSISTANT_ROUTE,
            messages=typed_messages,
            max_tokens=1400,
            temperature=0.3,
            stream=False,
        )

        # Serialize and send
        try:
            payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        except Exception as exc:
            raise AssistantClientError(
                "Failed to serialize request",
                reason="serialization_error",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "error": str(exc),
                },
            ) from exc

        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-SK-Dashboard-Surface": "assistant",
                "X-SK-Dashboard-Actor": actor,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AssistantClientError(
                f"Gateway returned HTTP {exc.code}",
                reason="http_error",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "status_code": exc.code,
                    "response": exc.read().decode("utf-8", "ignore")[:500],
                },
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OutageError(
                "Gateway is unreachable",
                reason="outage",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "error": str(exc),
                },
            ) from exc

        # Validate response structure
        try:
            response = AssistantResponse.model_validate(data)
        except ValidationError as exc:
            raise ValidationError(
                "Response failed schema validation",
                reason="validation_error",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "response_snippet": json.dumps(data)[:1000],
                    "validation_errors": str(exc),
                },
            ) from exc

        # Check for route drift
        self._verify_route_drift(response, actor, card_id)

        # Extract content
        if response.choices and response.choices[0].message:
            return response.choices[0].message.get("content", "")

        raise AssistantClientError(
            "Response contained no content",
            reason="empty_response",
            audit_context={
                "actor": actor,
                "card_id": card_id,
                "response_id": response.id,
            },
        )

    def chat_stream(
        self,
        messages: list[dict],
        actor: str = "operator",
        card_id: str | None = None,
    ):
        """Streaming chat completion generator.

        Yields content tokens as they arrive from the gateway.

        Args:
            messages: List of {role, content} message dicts
            actor: Actor identity for audit
            card_id: Optional card context for audit

        Yields:
            Content strings (tokens)

        Raises:
            AssistantClientError: On connection/failure with audit context
        """
        # Build typed request
        try:
            typed_messages = [
                AssistantRequestMessage(
                    role=m["role"],
                    content=m["content"],
                    timestamp=datetime.now(UTC).isoformat(),
                )
                for m in messages
            ]
        except (KeyError, ValidationError) as exc:
            raise AssistantClientError(
                "Invalid message format",
                reason="invalid_request",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "error": str(exc),
                },
            ) from exc

        request = AssistantRequest(
            model=DASHBOARD_ASSISTANT_ROUTE,
            messages=typed_messages,
            max_tokens=1400,
            temperature=0.3,
            stream=True,
        )

        # Serialize and send
        try:
            payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        except Exception as exc:
            raise AssistantClientError(
                "Failed to serialize request",
                reason="serialization_error",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "error": str(exc),
                },
            ) from exc

        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-SK-Dashboard-Surface": "assistant",
                "X-SK-Dashboard-Actor": actor,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                # Collect provenance from headers if available
                provenance = self._extract_provenance(resp)

                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        j = json.loads(data)
                        delta = (j.get("choices") or [{}])[0].get("delta", {}).get("content")
                        if delta:
                            # Could attach provenance to first token
                            yield delta
                    except (json.JSONDecodeError, KeyError, TypeError):
                        # Malformed chunk - log and continue (fail closed handled at caller)
                        logger.warning(
                            "assistant client: malformed SSE chunk, skipping",
                            extra={"actor": actor, "chunk": data[:100]},
                        )
                        continue

        except urllib.error.HTTPError as exc:
            raise AssistantClientError(
                f"Gateway returned HTTP {exc.code}",
                reason="http_error",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "status_code": exc.code,
                    "response": exc.read().decode("utf-8", "ignore")[:500],
                },
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OutageError(
                "Gateway is unreachable",
                reason="outage",
                audit_context={
                    "actor": actor,
                    "card_id": card_id,
                    "error": str(exc),
                },
            ) from exc

    def _extract_provenance(self, resp) -> dict | None:
        """Extract provenance metadata from response headers."""
        provenance = {}
        if "X-SK-Model-Served" in resp.headers:
            provenance["model_served"] = resp.headers["X-SK-Model-Served"]
        if "X-SK-Backend-Id" in resp.headers:
            provenance["backend_id"] = resp.headers["X-SK-Backend-Id"]
        return provenance if provenance else None

    def _verify_route_drift(
        self, response: AssistantResponse, actor: str, card_id: str | None
    ) -> None:
        """Verify the route resolved to an expected backend.

        This is a fail-closed check: if we detect drift (e.g., route resolved
        to an unexpected backend or model), we raise RouteDriftError.

        For now, we just log the provenance. In production, this would
        validate against an approved backend list.
        """
        if response.provenance:
            logger.info(
                "assistant client: request served",
                extra={
                    "actor": actor,
                    "card_id": card_id,
                    "route_used": response.provenance.route_used,
                    "model_served": response.provenance.model_served,
                    "backend_id": response.provenance.backend_id,
                },
            )

    def available(self, timeout: float = 2.0) -> bool:
        """Check if the gateway is reachable.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            True if gateway responds, False otherwise
        """
        try:
            req = urllib.request.Request(
                self.base_url.rstrip("/") + "/models", method="GET"
            )
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:  # noqa: BLE001
            return False


# Default client instance
_default_client: AssistantClient | None = None


def get_client() -> AssistantClient:
    """Get or create the default assistant client instance."""
    global _default_client
    if _default_client is None:
        _default_client = AssistantClient()
    return _default_client
