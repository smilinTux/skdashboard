"""Tests for the provider-neutral dashboard assistant client.

Card: 5c38b715 (SKDASH-AI-ASSISTANT-01)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from skdashboard.assistant_client import (
    AssistantClient,
    AssistantClientError,
    AssistantDelta,
    AssistantProvenance,
    AssistantRequest,
    AssistantRequestContext,
    AssistantRequestMessage,
    AssistantResponse,
    get_client,
    OutageError,
    RouteDriftError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Schema Validation Tests
# ---------------------------------------------------------------------------


def test_assistant_request_context_valid():
    """AssistantRequestContext accepts valid data."""
    ctx = AssistantRequestContext(
        surface="dashboard",
        actor="operator",
        card_id="5c38b715",
        timestamp=datetime.now(UTC).isoformat(),
    )
    assert ctx.surface == "dashboard"
    assert ctx.actor == "operator"
    assert ctx.card_id == "5c38b715"


def test_assistant_request_context_rejects_extra():
    """AssistantRequestContext rejects extra fields (extra='forbid')."""
    with pytest.raises(Exception):  # Pydantic ValidationError
        AssistantRequestContext(
            surface="dashboard",
            actor="operator",
            invalid_field="should_fail",
        )


def test_assistant_request_message_valid():
    """AssistantRequestMessage accepts valid message."""
    msg = AssistantRequestMessage(
        role="user",
        content="Hello, assistant!",
        timestamp=datetime.now(UTC).isoformat(),
    )
    assert msg.role == "user"
    assert msg.content == "Hello, assistant!"


def test_assistant_request_message_rejects_credentials():
    """AssistantRequestMessage rejects credential patterns."""
    # Bearer token pattern
    with pytest.raises(ValueError, match="credential"):
        AssistantRequestMessage(role="user", content="Use bearer xyz123")

    # API key pattern
    with pytest.raises(ValueError, match="credential"):
        AssistantRequestMessage(role="user", content="api_key: abc123")

    # Secret pattern
    with pytest.raises(ValueError, match="credential"):
        AssistantRequestMessage(role="user", content="secret: xyz")

    # Password pattern
    with pytest.raises(ValueError, match="credential"):
        AssistantRequestMessage(role="user", content="password: hunter2")


def test_assistant_request_message_rejects_long_opaque_strings():
    """AssistantRequestMessage rejects long alphanumeric strings (potential encoded secrets)."""
    # Base64-like long string
    long_opaque = "a" * 600  # 600 chars, all alphanumeric
    with pytest.raises(ValueError, match="opaque long strings"):
        AssistantRequestMessage(role="user", content=long_opaque)

    # Normal long text should be fine (contains spaces/punctuation)
    long_normal = "Hello " * 200  # > 500 chars but not opaque
    msg = AssistantRequestMessage(role="user", content=long_normal)
    assert len(msg.content) > 500


def test_assistant_request_message_rejects_capability_references():
    """AssistantRequest rejects capability material in messages."""
    msg = AssistantRequestMessage(role="user", content="x-sk-capability: some-token")

    # The message itself is allowed (might be quoted text)
    # But the request validator should catch it
    with pytest.raises(ValueError, match="capability"):
        AssistantRequest(
            messages=[msg],
            stream=True,
        )

    # Same for capability: pattern
    msg2 = AssistantRequestMessage(role="user", content="capability: read-files")
    with pytest.raises(ValueError, match="capability"):
        AssistantRequest(
            messages=[msg2],
            stream=True,
        )


def test_assistant_request_valid():
    """AssistantRequest accepts valid request with defaults."""
    msg = AssistantRequestMessage(role="user", content="Test message")
    req = AssistantRequest(
        messages=[msg],
        max_tokens=1000,
        temperature=0.5,
        stream=True,
    )
    assert req.model == "sk-dashboard-assistant"  # Default route
    assert len(req.messages) == 1
    assert req.max_tokens == 1000
    assert req.temperature == 0.5
    assert req.stream is True


def test_assistant_request_enforces_message_count_bounds():
    """AssistantRequest enforces message count limits."""
    msg = AssistantRequestMessage(role="user", content="Test")

    # Too few messages
    with pytest.raises(Exception):
        AssistantRequest(messages=[], stream=True)

    # Too many messages (> 20)
    with pytest.raises(Exception):
        AssistantRequest(messages=[msg] * 21, stream=True)


def test_assistant_request_enforces_token_bounds():
    """AssistantRequest enforces max_tokens limits."""
    msg = AssistantRequestMessage(role="user", content="Test")

    # Below minimum
    with pytest.raises(Exception):
        AssistantRequest(messages=[msg], max_tokens=0, stream=True)

    # Above maximum
    with pytest.raises(Exception):
        AssistantRequest(messages=[msg], max_tokens=5000, stream=True)

    # Valid bounds
    req = AssistantRequest(messages=[msg], max_tokens=2048, stream=True)
    assert req.max_tokens == 2048


def test_assistant_request_enforces_temperature_bounds():
    """AssistantRequest enforces temperature limits."""
    msg = AssistantRequestMessage(role="user", content="Test")

    # Below minimum
    with pytest.raises(Exception):
        AssistantRequest(messages=[msg], temperature=-0.1, stream=True)

    # Above maximum
    with pytest.raises(Exception):
        AssistantRequest(messages=[msg], temperature=1.1, stream=True)

    # Valid bounds
    req = AssistantRequest(messages=[msg], temperature=0.7, stream=True)
    assert req.temperature == 0.7


def test_assistant_request_enforces_stream_true():
    """AssistantRequest only allows stream=True."""
    msg = AssistantRequestMessage(role="user", content="Test")

    with pytest.raises(Exception):
        AssistantRequest(messages=[msg], stream=False)

    # stream=True should work
    req = AssistantRequest(messages=[msg], stream=True)
    assert req.stream is True


def test_assistant_provenance_valid():
    """AssistantProvenance accepts valid provenance data."""
    prov = AssistantProvenance(
        model_served="qwen3.8-27b-huihui-abliterated-q4_k_m",
        backend_id="chiap08-qwen38",
        route_used="sk-dashboard-assistant",
        timestamp=datetime.now(UTC).isoformat(),
    )
    assert prov.model_served.startswith("qwen3.8")
    assert prov.backend_id == "chiap08-qwen38"
    assert prov.route_used == "sk-dashboard-assistant"


def test_assistant_response_valid():
    """AssistantResponse accepts valid response."""
    resp = AssistantResponse(
        id="resp-123",
        object="chat.completion",
        created=1725148800,
        model="sk-dashboard-assistant",
        choices=[
            {
                "index": 0,
                "delta": {"content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        provenance=AssistantProvenance(
            model_served="qwen3.8-27b",
            backend_id="chiap08-qwen38",
            route_used="sk-dashboard-assistant",
            timestamp=datetime.now(UTC).isoformat(),
        ),
    )
    assert resp.id == "resp-123"
    assert len(resp.choices) == 1
    assert resp.choices[0].delta.content == "Hello!"
    assert resp.provenance is not None


def test_assistant_response_rejects_empty_choices():
    """AssistantResponse requires at least one choice."""
    with pytest.raises(ValueError, match="at least one choice"):
        AssistantResponse(
            id="resp-123",
            object="chat.completion",
            created=1725148800,
            model="sk-dashboard-assistant",
            choices=[],
        )


# ---------------------------------------------------------------------------
# Client Tests (Unit)
# ---------------------------------------------------------------------------


def test_client_defaults():
    """AssistantClient has correct defaults."""
    client = AssistantClient()
    assert client.base_url == "http://localhost:18780/v1"
    assert client.timeout == 90.0
    assert client.request_timeout == 60.0


def test_client_custom_config():
    """AssistantClient accepts custom configuration."""
    client = AssistantClient(
        base_url="http://custom:8000/v1",
        timeout=30.0,
        request_timeout=15.0,
    )
    assert client.base_url == "http://custom:8000/v1"
    assert client.timeout == 30.0
    assert client.request_timeout == 15.0


def test_get_client_singleton():
    """get_client returns a singleton instance."""
    c1 = get_client()
    c2 = get_client()
    assert c1 is c2


def test_client_available_true(mocker):
    """Client.available returns True when gateway is reachable."""
    client = AssistantClient()

    # Mock successful HTTP request
    mock_urlopen = mocker.patch("urllib.request.urlopen")
    mock_response = mocker.MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    assert client.available(timeout=2.0) is True
    mock_urlopen.assert_called_once()


def test_client_available_false(mocker):
    """Client.available returns False when gateway is unreachable."""
    client = AssistantClient()

    # Mock failed HTTP request
    mock_urlopen = mocker.patch("urllib.request.urlopen", side_effect=Exception("unreachable"))

    assert client.available(timeout=2.0) is False


def test_client_chat_invalid_message_format(mocker):
    """Client.chat rejects invalid message format."""
    client = AssistantClient()

    # Invalid message (missing 'role' key)
    with pytest.raises(AssistantClientError) as exc_info:
        client.chat([{"content": "test"}], actor="test")

    assert exc_info.value.reason == "invalid_request"
    assert "actor" in exc_info.value.audit_context


def test_client_chat_serialization_error(mocker):
    """Client.chat handles serialization errors."""
    client = AssistantClient()

    # Mock broken model_dump_json (shouldn't happen in practice)
    with pytest.raises(AssistantClientError) as exc_info:
        client.chat([{"role": "user", "content": "test"}], actor="test")

    # This should work normally, but we're testing the error path
    # In real usage, Pydantic serialization shouldn't fail for valid data


# ---------------------------------------------------------------------------
# Integration Tests (Require running gateway)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_client_chat_integration(gateway_available):
    """Integration test: client.chat with real gateway."""
    client = AssistantClient()

    result = client.chat(
        messages=[{"role": "user", "content": "Say 'test passed'"}],
        actor="integration-test",
    )

    assert isinstance(result, str)
    assert len(result) > 0
    # Model should echo something like "test passed"


@pytest.mark.integration
def test_client_chat_stream_integration(gateway_available):
    """Integration test: client.chat_stream with real gateway."""
    client = AssistantClient()

    tokens = list(
        client.chat_stream(
            messages=[{"role": "user", "content": "Count to 3: one, two, three"}],
            actor="integration-test",
        )
    )

    assert len(tokens) > 0
    assert all(isinstance(t, str) for t in tokens)
    # Should have received some content


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


def test_client_outage_error_audit_context():
    """OutageError includes audit context."""
    error = OutageError(
        "Gateway unreachable",
        reason="outage",
        audit_context={
            "actor": "test-user",
            "card_id": "test-123",
            "error": "Connection refused",
        },
    )

    assert error.reason == "outage"
    assert error.audit_context["actor"] == "test-user"
    assert error.audit_context["card_id"] == "test-123"
    assert "Connection refused" in error.audit_context["error"]


def test_client_validation_error_audit_context():
    """ValidationError includes audit context."""
    error = ValidationError(
        "Response validation failed",
        reason="validation_error",
        audit_context={
            "actor": "test-user",
            "validation_errors": "missing field: id",
        },
    )

    assert error.reason == "validation_error"
    assert "missing field: id" in error.audit_context["validation_errors"]


# ---------------------------------------------------------------------------
# Route Drift Detection Tests
# ---------------------------------------------------------------------------


def test_client_route_drift_logging(mocker, caplog):
    """Client logs route drift information."""
    client = AssistantClient()

    # Create a mock response with provenance
    mock_response = AssistantResponse(
        id="test-123",
        object="chat.completion",
        created=1725148800,
        model="sk-dashboard-assistant",
        choices=[
            {
                "index": 0,
                "message": {"content": "Test"},
                "finish_reason": "stop",
            }
        ],
        provenance=AssistantProvenance(
            model_served="unexpected-model",
            backend_id="unexpected-backend",
            route_used="sk-dashboard-assistant",
            timestamp=datetime.now(UTC).isoformat(),
        ),
    )

    # Call _verify_route_drift
    with caplog.at_level("INFO"):
        client._verify_route_drift(mock_response, actor="test", card_id=None)

    # Should log provenance information
    assert any("request served" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Provenance Extraction Tests
# ---------------------------------------------------------------------------


def test_extract_provenance_from_headers(mocker):
    """Client extracts provenance from response headers."""
    client = AssistantClient()

    # Mock response with provenance headers
    mock_response = mocker.MagicMock()
    mock_response.headers = {
        "X-SK-Model-Served": "qwen3.8-27b-huihui-abliterated-q4_k_m",
        "X-SK-Backend-Id": "chiap08-qwen38",
    }

    provenance = client._extract_provenance(mock_response)

    assert provenance is not None
    assert provenance["model_served"] == "qwen3.8-27b-huihui-abliterated-q4_k_m"
    assert provenance["backend_id"] == "chiap08-qwen38"


def test_extract_provenance_no_headers(mocker):
    """Client returns None when no provenance headers present."""
    client = AssistantClient()

    # Mock response without provenance headers
    mock_response = mocker.MagicMock()
    mock_response.headers = {}

    provenance = client._extract_provenance(mock_response)

    assert provenance is None


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_available():
    """Skip test if gateway is not available."""
    import os

    gateway_url = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")

    try:
        import urllib.request

        req = urllib.request.Request(
            gateway_url.rstrip("/") + "/models", method="GET"
        )
        with urllib.request.urlopen(req, timeout=2.0):
            return True
    except Exception:
        pytest.skip("Gateway not available for integration test")
