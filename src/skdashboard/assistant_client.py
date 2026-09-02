"""Provider-neutral, read-only dashboard assistant through SKGateway.

The dashboard never opens a provider connection. It passes a bounded message
contract to the shared SKGateway client with one deployment-configured logical
route. No tools, capabilities, credentials, or action schema are accepted.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal, TypedDict

logger = logging.getLogger("skdashboard.assistant_client")

DEFAULT_ROUTE = "sk-dashboard-assistant"
MAX_MESSAGES = 20
MAX_CONTENT_CHARS = 24_000
MAX_TOKENS = 1_400
_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})
_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer|x-sk-capability\s*:|"
    r"api[_-]?key\s*:|password\s*:|secret\s*:|oauth[_-]?token\s*:)"
)
_PROTECTED_KEYS = frozenset(
    {
        "prompt",
        "response",
        "tool_input",
        "tool_output",
        "workspace_path",
        "source_path",
        "session_id",
        "credential",
        "capability",
        "capability_token",
        "api_key",
        "cookie",
        "oauth_token",
    }
)


class AssistantMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class AssistantClientError(RuntimeError):
    """A fail-closed assistant request failure with safe audit fields."""

    def __init__(self, reason: str, *, actor: str, detail: str = "") -> None:
        self.reason = reason
        self.audit_context = {
            "actor": _bounded_actor(actor),
            "route": configured_route(),
            "detail": detail[:160],
        }
        super().__init__(reason)


def _bounded_actor(actor: object) -> str:
    if not isinstance(actor, str) or not actor.strip():
        return "unattributed"
    return actor.strip()[:128]


def configured_route(environ: dict[str, str] | None = None) -> str:
    """Return one bounded logical route, never a provider/model identifier."""
    values = os.environ if environ is None else environ
    route = values.get("SKDASHBOARD_ASSISTANT_ROUTE", DEFAULT_ROUTE).strip()
    if not route or len(route) > 128 or not re.fullmatch(r"[a-z][a-z0-9-]*", route):
        raise ValueError("SKDASHBOARD_ASSISTANT_ROUTE must be a bounded logical route")
    return route


def validate_messages(messages: Iterable[dict]) -> list[AssistantMessage]:
    """Validate the exact no-tools request contract before gateway handoff."""
    if not isinstance(messages, (list, tuple)):
        raise ValueError("messages must be a bounded sequence")
    if not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError("messages must contain between 1 and 20 entries")
    result: list[AssistantMessage] = []
    total = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("assistant messages use only role and content")
        role = message.get("role")
        content = message.get("content")
        if role not in _ALLOWED_ROLES or not isinstance(content, str) or not content.strip():
            raise ValueError("assistant message role or content is invalid")
        total += len(content)
        if total > MAX_CONTENT_CHARS:
            raise ValueError("assistant request content exceeds its bound")
        if _SECRET_PATTERN.search(content):
            raise ValueError("assistant request contains credential-shaped material")
        lowered = content.lower()
        if any(f'"{key}"' in lowered for key in _PROTECTED_KEYS):
            raise ValueError("assistant request contains a protected payload field")
        result.append({"role": role, "content": content})
    return result


@dataclass(frozen=True, slots=True)
class AssistantClient:
    """Read-only facade over the approved shared SKGateway abstraction."""

    route: str | None = None
    timeout: float = 90.0

    def chat_stream(self, messages: list[dict], *, actor: str = "unattributed"):
        try:
            safe_messages = validate_messages(messages)
            route = self.route or configured_route()
            if route != configured_route():
                raise ValueError("assistant route must equal the approved configured route")
        except ValueError as exc:
            raise AssistantClientError("invalid_request", actor=actor, detail=str(exc)) from exc

        # This is the sole inference boundary. The dashboard does not import an
        # SDK for any provider and does not construct a provider URL.
        from skcapstone import skgateway_client

        try:
            yield from skgateway_client.chat_stream(
                safe_messages,
                model=route,
                max_tokens=MAX_TOKENS,
                temperature=0.3,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed at the abstraction
            logger.warning(
                "read-only assistant gateway failure",
                extra={"actor": _bounded_actor(actor), "route": route, "reason": type(exc).__name__},
            )
            raise AssistantClientError(
                "gateway_unavailable", actor=actor, detail=type(exc).__name__
            ) from exc


def get_client() -> AssistantClient:
    """Create a stateless client so deployment route changes are observed."""
    return AssistantClient()


__all__ = [
    "AssistantClient",
    "AssistantClientError",
    "DEFAULT_ROUTE",
    "configured_route",
    "get_client",
    "validate_messages",
]
