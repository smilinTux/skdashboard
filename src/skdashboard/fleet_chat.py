"""Bounded read-only projection over the append-only SKMail store."""

from __future__ import annotations

import heapq
import json
import os
import re
import stat
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MAX_MESSAGES = 400
MAX_FILES = 4096
MAX_LINE_BYTES = 256 * 1024
MAX_SUBJECT_CHARS = 512
MAX_BODY_CHARS = 4000
FRESHNESS_TTL_SECONDS = 60
REDACTED = "[REDACTED]"

_NAME = re.compile(
    r"^(?P<agent>[A-Za-z0-9._-]{1,160})@(?P<host>chi(?:ap|wk)\d{2})\.jsonl$"
)
_RECIPIENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_PRIORITY = {
    "critical": "urgent",
    "high": "urgent",
    "urgent": "urgent",
    "normal": "normal",
    "fyi": "fyi",
    "low": "fyi",
}
_WORKER = re.compile(
    r"^pi-(?P<lane>[a-z0-9-]+?)-(?:(?P<identity_host>chiap\d+|chiwk\d+)-)?"
    r"(?P<card>[0-9a-f]{6,8})$"
)
_SENSITIVE_KEY = re.compile(
    r"(?:auth|bearer|capability|credential|password|private[_-]?key|protected[_-]?payload|"
    r"secret|token|api[_-]?key|\bkey\b)",
    re.IGNORECASE,
)
_LABELED_SECRET = re.compile(
    r"(?i)\b(auth(?:orization)?|bearer|capability|credential|password|private[_-]?key|"
    r"protected[ _-]?payload|secret|token|api[_-]?key|key)\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)


def _mail_dir(home: Path) -> Path:
    return Path(home) / "coordination" / "skmail.d"


def _utc_instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _utc_timestamp(value: object) -> str | None:
    parsed = _utc_instant(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _redact_text(value: str) -> tuple[str, bool]:
    redacted = False

    def labeled(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match.group(1)}{match.group(2)}{REDACTED}"

    for pattern in (_PRIVATE_KEY, _BEARER, _OPENAI_KEY):
        value, count = pattern.subn(REDACTED, value)
        redacted = redacted or bool(count)
    value = _LABELED_SECRET.sub(labeled, value)
    return value, redacted


def _safe_body(value: object) -> tuple[str, bool]:
    redacted = False

    def scrub(item: object, depth: int = 0) -> object:
        nonlocal redacted
        if depth > 8:
            redacted = True
            return REDACTED
        if isinstance(item, dict):
            safe = {}
            for key, child in item.items():
                label = str(key)
                if _SENSITIVE_KEY.search(label):
                    safe[label] = REDACTED
                    redacted = True
                else:
                    safe[label] = scrub(child, depth + 1)
            return safe
        if isinstance(item, list):
            return [scrub(child, depth + 1) for child in item[:100]]
        if isinstance(item, str):
            safe, changed = _redact_text(item)
            redacted = redacted or changed
            return safe
        if item is None or isinstance(item, (bool, int, float)):
            return item
        redacted = True
        return REDACTED

    if isinstance(value, str):
        text, redacted = _redact_text(value)
    elif value is None:
        text = ""
    else:
        text = json.dumps(scrub(value), sort_keys=True, separators=(",", ":"))
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS]
        redacted = True
    return text, redacted


def _norm(record: object, agent: str, host: str) -> tuple[dict | None, str | None]:
    if not isinstance(record, dict):
        return None, "NON_OBJECT"

    claimed_sender = record.get("from")
    if claimed_sender is not None and claimed_sender != agent:
        return None, "WRITER_IDENTITY_MISMATCH"
    claimed_host = record.get("host")
    if claimed_host is not None and claimed_host != host:
        return None, "WRITER_HOST_MISMATCH"

    raw_to = record.get("to")
    if isinstance(raw_to, str):
        recipients = raw_to.split(",")
    elif isinstance(raw_to, list) and all(isinstance(value, str) for value in raw_to):
        recipients = raw_to
    else:
        return None, "INVALID_RECIPIENTS"
    recipients = [value.strip().lower() for value in recipients if value.strip()]
    if len(recipients) > 32 or any(len(value) > 160 for value in recipients):
        return None, "INVALID_RECIPIENTS"
    recipient_redacted = False
    safe_recipients = []
    for recipient in recipients:
        safe, changed = _redact_text(recipient)
        if changed:
            safe = REDACTED
            recipient_redacted = True
        if safe != REDACTED and _RECIPIENT.fullmatch(safe) is None:
            return None, "INVALID_RECIPIENTS"
        safe_recipients.append(safe)

    ts = _utc_timestamp(record.get("ts") or record.get("sent_at"))
    if ts is None:
        return None, "INVALID_TIMESTAMP"

    subject = record.get("re")
    if not isinstance(subject, str):
        subject = record.get("subject") if isinstance(record.get("subject"), str) else ""
    subject, subject_redacted = _redact_text(subject[:MAX_SUBJECT_CHARS])
    body, body_redacted = _safe_body(record.get("body"))
    priority = record.get("priority")
    priority = (
        _PRIORITY.get(priority.strip().lower(), "normal")
        if isinstance(priority, str)
        else "normal"
    )
    item = {
        "ts": ts,
        "from": agent,
        "agent": agent,
        "sender_verified": True,
        "to": safe_recipients,
        "subject": subject,
        "body": body,
        "redacted": recipient_redacted or subject_redacted or body_redacted,
        "priority": priority,
        "host": host,
    }
    item.update(_classify(agent))
    return item, None


def _classify(agent: str) -> dict:
    match = _WORKER.match(agent)
    if match:
        return {"kind": "worker", "lane": match.group("lane"), "card": match.group("card")}
    if agent.lower() in {"jarvis", "lumina", "atlas", "codex-root", "aster"}:
        return {"kind": "agent", "lane": None, "card": None}
    return {"kind": "other", "lane": None, "card": None}


def _read_projection(home: Path, limit: int = MAX_MESSAGES) -> tuple[list[dict], Counter, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_MESSAGES:
        raise ValueError(f"limit must be between 1 and {MAX_MESSAGES}")
    mail_dir = _mail_dir(home)
    errors: Counter = Counter()
    heap: list[tuple[datetime, int, dict]] = []
    valid = 0
    sequence = 0
    if not mail_dir.is_dir():
        errors["SOURCE_UNAVAILABLE"] += 1
        return [], errors, valid

    try:
        entries = os.scandir(mail_dir)
        with entries:
            for file_index, entry in enumerate(entries):
                if file_index >= MAX_FILES:
                    errors["FILE_BOUND_EXCEEDED"] += 1
                    continue
                match = _NAME.fullmatch(entry.name)
                if match is None:
                    if entry.name.endswith(".jsonl"):
                        errors["INVALID_WRITER_FILE"] += 1
                    continue
                try:
                    descriptor = os.open(
                        entry.path,
                        os.O_RDONLY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                    )
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        os.close(descriptor)
                        errors["UNREADABLE_WRITER_FILE"] += 1
                        continue
                    with os.fdopen(descriptor, "rb") as handle:
                        while True:
                            raw = handle.readline(MAX_LINE_BYTES + 1)
                            if not raw:
                                break
                            if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
                                while raw and not raw.endswith(b"\n"):
                                    raw = handle.readline(MAX_LINE_BYTES + 1)
                                errors["OVERSIZED_RECORD"] += 1
                                continue
                            try:
                                record = json.loads(raw.decode("utf-8"))
                            except (UnicodeDecodeError, ValueError, RecursionError):
                                errors["INVALID_JSON"] += 1
                                continue
                            item, error = _norm(
                                record, match.group("agent"), match.group("host")
                            )
                            if error is not None:
                                errors[error] += 1
                                continue
                            valid += 1
                            sequence += 1
                            instant = _utc_instant(item["ts"])
                            assert instant is not None
                            candidate = (instant, sequence, item)
                            if len(heap) < limit:
                                heapq.heappush(heap, candidate)
                            elif candidate[:2] > heap[0][:2]:
                                heapq.heapreplace(heap, candidate)
                except OSError:
                    errors["UNREADABLE_WRITER_FILE"] += 1
    except OSError:
        errors["SOURCE_UNAVAILABLE"] += 1
    return [item for _instant, _sequence, item in sorted(heap)], errors, valid


def read_messages(home: Path, limit: int = MAX_MESSAGES) -> list[dict]:
    """Return the newest verified messages, oldest first, with bounded memory."""

    messages, _errors, _valid = _read_projection(Path(home), limit)
    return messages


def channels(messages: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for message in messages:
        for name in message["to"] or ["unaddressed"]:
            channel = seen.setdefault(
                name, {"name": name, "count": 0, "last": "", "urgent": 0}
            )
            channel["count"] += 1
            if not channel["last"] or _utc_instant(message["ts"]) > _utc_instant(
                channel["last"]
            ):
                channel["last"] = message["ts"]
            if message["priority"] == "urgent":
                channel["urgent"] += 1
    return sorted(
        seen.values(), key=lambda channel: _utc_instant(channel["last"]), reverse=True
    )


def fleet_chat(home: Path, *, now: datetime | None = None) -> dict:
    messages, error_counts, valid = _read_projection(Path(home))
    speakers: dict[str, int] = {}
    for message in messages:
        speakers[message["from"]] = speakers.get(message["from"], 0) + 1
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    projected_at = instant.isoformat().replace("+00:00", "Z")
    errors = [
        {"code": code, "count": count}
        for code, count in sorted(error_counts.items())
        if count
    ]
    observed_at = messages[-1]["ts"] if messages else None
    age_seconds = None
    future_seconds = 0
    truth_state = "unknown"
    if observed_at is not None:
        observed = _utc_instant(observed_at)
        assert observed is not None
        age = (instant - observed).total_seconds()
        age_seconds = max(0, int(age))
        future_seconds = max(0, int(-age))
        if age < 0:
            truth_state = "unavailable"
        elif age_seconds > FRESHNESS_TTL_SECONDS:
            truth_state = "stale"
        elif errors:
            truth_state = "partial"
        else:
            truth_state = "current"
    return {
        "messages": messages,
        "channels": channels(messages),
        "speakers": sorted(
            ({"name": name, "count": count} for name, count in speakers.items()),
            key=lambda speaker: speaker["count"],
            reverse=True,
        )[:20],
        "total": len(messages),
        "source_total": valid,
        "source": "skmail.d",
        "read_only": True,
        "partial": bool(errors),
        "invalid_records": sum(error["count"] for error in errors),
        "errors": errors,
        "freshness": {
            "truth_state": truth_state,
            "observed_at": observed_at,
            "projected_at": projected_at,
            "age_seconds": age_seconds,
            "ttl_seconds": FRESHNESS_TTL_SECONDS,
            "future_seconds": future_seconds,
            "limit": MAX_MESSAGES,
            "truncated": valid > len(messages),
        },
    }
