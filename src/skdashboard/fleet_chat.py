"""Read-only channel view over the skmail store.

skmail is an append-only JSONL mailbox, one file per writer per host, living in
the Syncthing-shared coordination directory. Every host already has the whole
store locally, so this reader needs no service, no socket and no new transport:
it is a projection over files the dashboard host can already see.

Deliberately READ ONLY. skmail's integrity comes from a writer only ever
appending to its own `<agent>@<host>.jsonl`; a dashboard that wrote on someone
else's behalf would break the one rule that stops an agent forging mail as
another. Sending is a separate, later decision.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(r"^(?P<agent>.+)@(?P<host>[^@]+)\.jsonl$")
_PRIORITY = {"critical": "urgent", "high": "urgent", "urgent": "urgent",
             "normal": "normal", "fyi": "fyi", "low": "fyi"}
# A worker identity is pi-<lane>-<host>-<card> or pi-<lane>-<card>.
_WORKER = re.compile(r"^pi-(?P<lane>[a-z]+)-(?:(?P<host>chiap\d+|chiwk\d+)-)?(?P<card>[0-9a-f]{6,8})$")
# Secret-shaped patterns that must be redacted from recipients.
_SECRET_PATTERNS = [
    re.compile(r"password\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"pwd\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"secret\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"key\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"token\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*=\s*\S+", re.IGNORECASE),
]
# Valid recipient pattern: lowercase alphanumeric, hyphens, underscores, @, dots.
_VALID_RECIPIENT = re.compile(r"^[a-z0-9_\-@.]+$")
# Maximum recipient length to prevent abuse.
_MAX_RECIPIENT_LENGTH = 128
# TTL for considering a mailbox current: 24 hours.
_FRESHNESS_TTL_SECONDS = 86400.0


def _mail_dir(home: Path) -> Path:
    return Path(home) / "coordination" / "skmail.d"


def _parse_timestamp(ts: str) -> float | None:
    """Parse ISO timestamp to Unix timestamp (seconds since epoch).

    Handles mixed fractional widths and Z/+00:00 offsets by parsing to a
    datetime and converting to a canonical float. Returns None on parse failure.
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        # fromisoformat handles fractional seconds and Z if we normalize it
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # Ensure timezone-aware (treat naive as UTC for robustness)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _redact_secret(value: str) -> str:
    """Redact secret-shaped patterns from a value.

    If the value matches any secret pattern, return [REDACTED].
    Otherwise return the original value.
    """
    if not isinstance(value, str):
        return ""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            return "[REDACTED]"
    return value


def _validate_recipient(recipient: str) -> str | None:
    """Validate and optionally redact a recipient.

    Returns the validated/redacted recipient, or None if invalid.
    First checks for secret patterns; if found, returns [REDACTED].
    Otherwise validates against the allowed pattern.
    """
    if not isinstance(recipient, str):
        return None
    recipient = recipient.strip().lower()
    if not recipient:
        return None
    # Check for secret patterns first - redact to [REDACTED]
    for pattern in _SECRET_PATTERNS:
        if pattern.search(recipient):
            return "[REDACTED]"
    # Length check (skip for [REDACTED] which is already known-safe)
    if recipient != "[REDACTED]" and len(recipient) > _MAX_RECIPIENT_LENGTH:
        return None
    # Pattern check (skip for [REDACTED] which is allowed)
    if recipient != "[REDACTED]" and not _VALID_RECIPIENT.match(recipient):
        return None
    return recipient


def _norm(record: dict, agent: str | None, host: str | None) -> dict | None:
    """Normalise one record the same way the skmail reader does.

    Both message shapes are legitimate: `re` or `subject`, `to` as a string or
    a list. Validating more strictly than the tool that displays them would
    hide real messages, which is the failure this panel exists to end.
    """
    if not isinstance(record, dict):
        return None
    to = record.get("to")
    if isinstance(to, list) and all(isinstance(v, str) for v in to):
        to = ",".join(to)
    if not isinstance(to, str):
        to = ""
    # Parse and validate recipients
    recipients = []
    for v in to.split(","):
        validated = _validate_recipient(v)
        if validated:
            recipients.append(validated)
    subject = record.get("re")
    if not isinstance(subject, str):
        subject = record.get("subject") if isinstance(record.get("subject"), str) else ""
    body = record.get("body")
    if not isinstance(body, str):
        body = "" if body is None else json.dumps(body, indent=2)
    frm = record.get("from")
    if not isinstance(frm, str) or not frm.strip():
        frm = agent or "unknown"
    ts = record.get("ts") or record.get("sent_at")
    if not isinstance(ts, str):
        ts = ""
    priority = record.get("priority")
    priority = _PRIORITY.get(str(priority).strip().lower(), "normal") if isinstance(priority, str) else "normal"
    return {
        "ts": ts,
        "from": frm,
        "to": recipients,
        "subject": subject,
        "body": body,
        "priority": priority,
        "host": record.get("host") if isinstance(record.get("host"), str) else (host or ""),
    }


def _classify(frm: str) -> dict:
    """Who is speaking: a fleet worker, a named agent, or a person."""
    m = _WORKER.match(frm)
    if m:
        return {"kind": "worker", "lane": m.group("lane"), "card": m.group("card")}
    if frm.lower() in {"jarvis", "lumina", "atlas", "codex-root", "aster"}:
        return {"kind": "agent", "lane": None, "card": None}
    return {"kind": "other", "lane": None, "card": None}


def _compute_freshness(timestamps: list[float], now: float) -> dict:
    """Compute truth state, age, and TTL from observed timestamps.

    Args:
        timestamps: List of parsed Unix timestamps (floats).
        now: Current Unix timestamp.

    Returns:
        dict with truth_state ("current", "stale", "future", "empty"),
        age_seconds (oldest message age), ttl_seconds (remaining TTL),
        and observed_at (ISO string of newest timestamp).
    """
    if not timestamps:
        return {
            "truth_state": "empty",
            "age_seconds": None,
            "ttl_seconds": None,
            "observed_at": None,
        }
    newest = max(timestamps)
    oldest = min(timestamps)
    age = now - oldest
    ttl = _FRESHNESS_TTL_SECONDS - age

    # Check for future timestamps (clock skew or bad data)
    if newest > now:
        return {
            "truth_state": "future",
            "age_seconds": round(age, 1) if age >= 0 else None,
            "ttl_seconds": None,
            "observed_at": datetime.fromtimestamp(newest, tz=timezone.utc).isoformat(),
        }

    # Stale if oldest message is older than TTL
    if age > _FRESHNESS_TTL_SECONDS:
        return {
            "truth_state": "stale",
            "age_seconds": round(age, 1),
            "ttl_seconds": 0.0,
            "observed_at": datetime.fromtimestamp(newest, tz=timezone.utc).isoformat(),
        }

    # Current if within TTL window
    return {
        "truth_state": "current",
        "age_seconds": round(age, 1),
        "ttl_seconds": round(max(0.0, ttl), 1),
        "observed_at": datetime.fromtimestamp(newest, tz=timezone.utc).isoformat(),
    }


def read_messages(home: Path, limit: int = 400) -> list[dict]:
    """Every readable message, newest last, capped at `limit`.

    Parses timestamps to canonical Unix timestamps for correct ordering
    across mixed fractional widths and Z/+00:00 offsets.
    """
    d = _mail_dir(home)
    out: list[dict] = []
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.jsonl")):
        m = _NAME.match(path.name)
        agent = m.group("agent") if m else None
        host = m.group("host") if m else None
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                item = _norm(record, agent, host)
                if item is None or not item["ts"]:
                    continue
                # Parse timestamp to canonical instant for correct sorting
                ts_parsed = _parse_timestamp(item["ts"])
                if ts_parsed is None:
                    continue
                item["_ts_parsed"] = ts_parsed
                item.update(_classify(item["from"]))
                out.append(item)
    # Sort by parsed timestamp, not string, to handle mixed fractional widths
    out.sort(key=lambda r: r["_ts_parsed"])
    return out[-limit:]


def channels(messages: list[dict]) -> list[dict]:
    """A channel is a recipient. Ordered by most recent traffic."""
    seen: dict[str, dict] = {}
    for msg in messages:
        for name in msg["to"] or ["unaddressed"]:
            c = seen.setdefault(name, {"name": name, "count": 0, "last": "", "urgent": 0})
            c["count"] += 1
            c["last"] = max(c["last"], msg["ts"])
            if msg["priority"] == "urgent":
                c["urgent"] += 1
    return sorted(seen.values(), key=lambda c: c["last"], reverse=True)


def fleet_chat(home: Path) -> dict:
    messages = read_messages(Path(home))
    now = datetime.now(timezone.utc).timestamp()
    ts_parsed = [m["_ts_parsed"] for m in messages]
    freshness = _compute_freshness(ts_parsed, now)
    speakers: dict[str, int] = {}
    for m in messages:
        speakers[m["from"]] = speakers.get(m["from"], 0) + 1

    # Clean up internal _ts_parsed before returning
    for m in messages:
        m.pop("_ts_parsed", None)

    return {
        "messages": messages,
        "channels": channels(messages),
        "speakers": sorted(
            ({"name": k, "count": v} for k, v in speakers.items()),
            key=lambda s: s["count"], reverse=True)[:20],
        "total": len(messages),
        "source": "skmail",
        "read_only": True,
        "truth_state": freshness["truth_state"],
        "age_seconds": freshness["age_seconds"],
        "ttl_seconds": freshness["ttl_seconds"],
        "observed_at": freshness["observed_at"],
    }
