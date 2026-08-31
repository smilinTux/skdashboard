"""Regression tests for fleet_chat repairs (card d8f3a1c6).

Tests for:
1. Canonical timestamp comparison with mixed ISO fractional widths
2. Truthful freshness classification (stale/future/current)
3. Bounded recipient validation and secret-shaped redaction
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skdashboard.fleet_chat import (
    _compute_freshness,
    _parse_timestamp,
    _redact_secret,
    _validate_recipient,
    channels,
    fleet_chat,
    read_messages,
)


def test_parse_timestamp_mixed_fractional_widths():
    """Parse timestamps with different fractional widths correctly."""
    # Zero fractional seconds with Z
    ts1 = "2026-08-31T00:00:00Z"
    parsed1 = _parse_timestamp(ts1)
    assert parsed1 is not None
    assert isinstance(parsed1, float)

    # With fractional seconds with Z
    ts2 = "2026-08-31T00:00:00.100000Z"
    parsed2 = _parse_timestamp(ts2)
    assert parsed2 is not None
    assert isinstance(parsed2, float)

    # Zero fractional should be EARLIER than fractional
    assert parsed1 < parsed2

    # With +00:00 offset
    ts3 = "2026-08-31T00:00:00+00:00"
    parsed3 = _parse_timestamp(ts3)
    assert parsed3 is not None
    assert parsed1 == pytest.approx(parsed3)

    # With negative offset
    ts4 = "2026-08-31T00:00:00-05:00"
    parsed4 = _parse_timestamp(ts4)
    assert parsed4 is not None
    # -05:00 is 5 hours ahead of UTC, so should be later
    assert parsed4 > parsed1


def test_parse_timestamp_invalid():
    """Return None for invalid timestamps."""
    assert _parse_timestamp("") is None
    assert _parse_timestamp(None) is None
    assert _parse_timestamp("not-a-timestamp") is None
    assert _parse_timestamp(123) is None


def test_redact_secret_patterns():
    """Redact secret-shaped patterns from values."""
    assert _redact_secret("password=secret123") == "[REDACTED]"
    assert _redact_secret("pwd=hiddentext") == "[REDACTED]"
    assert _redact_secret("secret=mykey") == "[REDACTED]"
    assert _redact_secret("key=abcdef") == "[REDACTED]"
    assert _redact_secret("token=xyz789") == "[REDACTED]"
    assert _redact_secret("api_key=abc123") == "[REDACTED]"
    # Case insensitive
    assert _redact_secret("PASSWORD=secret") == "[REDACTED]"
    # Multiple patterns
    assert _redact_secret("user=alice password=secret") == "[REDACTED]"
    # No match
    assert _redact_secret("user=alice") == "user=alice"


def test_validate_recipient_valid():
    """Accept valid recipient formats."""
    assert _validate_recipient("jarvis") == "jarvis"
    assert _validate_recipient("lumina") == "lumina"
    assert _validate_recipient("pi-glm-chiap01-123456") == "pi-glm-chiap01-123456"
    assert _validate_recipient("agent@host") == "agent@host"
    assert _validate_recipient("user.name@example.com") == "user.name@example.com"
    assert _validate_recipient("all") == "all"


def test_validate_recipient_invalid():
    """Reject invalid recipient formats."""
    # Too long
    long_name = "a" * 129
    assert _validate_recipient(long_name) is None
    # Invalid characters
    assert _validate_recipient("user@example!") is None
    assert _validate_recipient("user#tag") is None
    assert _validate_recipient("user space") is None
    # Empty after strip
    assert _validate_recipient("   ") is None
    # None
    assert _validate_recipient(None) is None
    # Non-string
    assert _validate_recipient(123) is None


def test_validate_recipient_redacts_secrets():
    """Redact secret-shaped recipients to [REDACTED]."""
    result = _validate_recipient("password=visible-value")
    assert result == "[REDACTED]"


def test_compute_freshness_empty():
    """Empty timestamp list returns empty state."""
    now = datetime.now(timezone.utc).timestamp()
    result = _compute_freshness([], now)
    assert result["truth_state"] == "empty"
    assert result["age_seconds"] is None
    assert result["ttl_seconds"] is None
    assert result["observed_at"] is None


def test_compute_freshness_current():
    """Recent messages within TTL are current."""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    # 1 hour old and 2 hours old (within 24 hour TTL)
    ts_1h = now - 3600
    ts_2h = now - 7200
    result = _compute_freshness([ts_1h, ts_2h], now)
    assert result["truth_state"] == "current"
    assert result["age_seconds"] == 7200.0
    assert result["ttl_seconds"] == pytest.approx(86400.0 - 7200.0)
    assert result["observed_at"] is not None


def test_compute_freshness_stale():
    """Old messages outside TTL are stale."""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    # 25 hours old (outside 24 hour TTL)
    ts_old = now - (25 * 3600)
    result = _compute_freshness([ts_old], now)
    assert result["truth_state"] == "stale"
    assert result["age_seconds"] == 25 * 3600.0
    assert result["ttl_seconds"] == 0.0


def test_compute_freshness_stale_2020_mailbox():
    """A 2020-only mailbox is never current in 2026."""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    # 2020 timestamp (many years old)
    ts_2020 = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    result = _compute_freshness([ts_2020], now)
    assert result["truth_state"] == "stale"
    assert result["age_seconds"] > (6 * 365 * 24 * 3600)  # > 6 years
    assert result["ttl_seconds"] == 0.0


def test_compute_freshness_future():
    """Future timestamps indicate clock skew or bad data."""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    # 1 hour in the future
    ts_future = now + 3600
    result = _compute_freshness([ts_future], now)
    assert result["truth_state"] == "future"
    assert result["ttl_seconds"] is None


def test_read_messages_orders_by_parsed_timestamp():
    """Messages are ordered by parsed timestamp, not string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        mail_dir = tmppath / "coordination" / "skmail.d"
        mail_dir.mkdir(parents=True)

        # Create a mailbox file with mixed timestamp formats
        mailbox = mail_dir / "test@host.jsonl"
        records = [
            {"from": "test", "to": "all", "ts": "2026-08-31T00:00:00Z", "subject": "zero frac", "body": "msg"},
            {"from": "test", "to": "all", "ts": "2026-08-31T00:00:00.100000Z", "subject": "later", "body": "msg"},
        ]
        with mailbox.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        messages = read_messages(tmppath)
        assert len(messages) == 2
        # The zero-fraction message should come first (earlier)
        assert messages[0]["subject"] == "zero frac"
        assert messages[1]["subject"] == "later"


def test_read_messages_validates_and_redacts_recipients():
    """Invalid recipients are dropped, secrets are redacted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        mail_dir = tmppath / "coordination" / "skmail.d"
        mail_dir.mkdir(parents=True)

        mailbox = mail_dir / "test@host.jsonl"
        records = [
            {"from": "test", "to": "jarvis", "ts": "2026-08-31T12:00:00Z", "subject": "valid", "body": "msg"},
            {"from": "test", "to": "password=secret", "ts": "2026-08-31T12:01:00Z", "subject": "secret", "body": "msg"},
            {"from": "test", "to": "invalid!recipient", "ts": "2026-08-31T12:02:00Z", "subject": "invalid", "body": "msg"},
        ]
        with mailbox.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        messages = read_messages(tmppath)
        # Valid and [REDACTED] messages preserved, invalid recipient message dropped
        messages_with_recipients = [m for m in messages if m["to"]]
        assert len(messages_with_recipients) == 2

        # Check valid recipient preserved
        valid_msg = next(m for m in messages if m["subject"] == "valid")
        assert "jarvis" in valid_msg["to"]

        # Check secret redacted
        secret_msg = next(m for m in messages if m["subject"] == "secret")
        assert "[REDACTED]" in secret_msg["to"]


def test_fleet_chat_includes_freshness():
    """fleet_chat returns truth state, age, TTL, and observed_at."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        mail_dir = tmppath / "coordination" / "skmail.d"
        mail_dir.mkdir(parents=True)

        mailbox = mail_dir / "test@host.jsonl"
        # Recent message within TTL
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(hours=1)).isoformat()
        records = [
            {"from": "test", "to": "all", "ts": recent_ts, "subject": "recent", "body": "msg"},
        ]
        with mailbox.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = fleet_chat(tmppath)
        assert "truth_state" in result
        assert "age_seconds" in result
        assert "ttl_seconds" in result
        assert "observed_at" in result
        assert result["truth_state"] == "current"
        assert result["age_seconds"] > 0
        assert result["ttl_seconds"] > 0


def test_fleet_chat_stale_2020_data():
    """2020-only mailbox reports as stale, not current."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        mail_dir = tmppath / "coordination" / "skmail.d"
        mail_dir.mkdir(parents=True)

        mailbox = mail_dir / "test@host.jsonl"
        # 2020 message
        records = [
            {"from": "test", "to": "all", "ts": "2020-01-01T00:00:00Z", "subject": "old", "body": "msg"},
        ]
        with mailbox.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = fleet_chat(tmppath)
        assert result["truth_state"] == "stale"
        assert result["age_seconds"] is not None
        assert result["age_seconds"] > (6 * 365 * 24 * 3600)
        assert result["ttl_seconds"] == 0.0


def test_channels_uses_validated_recipients():
    """Channels only include validated/redacted recipients."""
    messages = [
        {"to": ["jarvis"], "ts": "2026-08-31T12:00:00Z", "priority": "normal"},
        {"to": ["password=secret"], "ts": "2026-08-31T12:01:00Z", "priority": "normal"},
        {"to": ["invalid!"], "ts": "2026-08-31T12:02:00Z", "priority": "normal"},
    ]

    # First normalize messages like read_messages does
    for m in messages:
        recipients = []
        for r in m["to"]:
            validated = _validate_recipient(r)
            if validated:
                recipients.append(validated)
        m["to"] = recipients

    chans = channels(messages)
    channel_names = [c["name"] for c in chans]
    assert "jarvis" in channel_names
    assert "[REDACTED]" in channel_names
    assert "password=secret" not in channel_names
    assert "invalid!" not in channel_names


def test_read_messages_respects_limit():
    """read_messages respects the limit parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        mail_dir = tmppath / "coordination" / "skmail.d"
        mail_dir.mkdir(parents=True)

        mailbox = mail_dir / "test@host.jsonl"
        # Create 500 messages
        records = []
        for i in range(500):
            hour = 12 + i // 60
            minute = i % 60
            ts = f"2026-08-31T{hour:02d}:{minute:02d}:00Z"
            records.append({
                "from": "test", "to": "all", "ts": ts, "subject": f"msg{i}", "body": "body"
            })
        with mailbox.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        messages = read_messages(tmppath, limit=400)
        assert len(messages) == 400
