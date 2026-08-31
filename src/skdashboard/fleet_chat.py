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
import os
import re
from pathlib import Path

_NAME = re.compile(r"^(?P<agent>.+)@(?P<host>[^@]+)\.jsonl$")
_PRIORITY = {"critical": "urgent", "high": "urgent", "urgent": "urgent",
             "normal": "normal", "fyi": "fyi", "low": "fyi"}
# A worker identity is pi-<lane>-<host>-<card> or pi-<lane>-<card>.
_WORKER = re.compile(r"^pi-(?P<lane>[a-z]+)-(?:(?P<host>chiap\d+|chiwk\d+)-)?(?P<card>[0-9a-f]{6,8})$")


def _mail_dir(home: Path) -> Path:
    return Path(home) / "coordination" / "skmail.d"


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
        "to": [v.strip().lower() for v in to.split(",") if v.strip()],
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


def read_messages(home: Path, limit: int = 400) -> list[dict]:
    """Every readable message, newest last, capped at `limit`."""
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
                item.update(_classify(item["from"]))
                out.append(item)
    out.sort(key=lambda r: r["ts"])
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
    speakers: dict[str, int] = {}
    for m in messages:
        speakers[m["from"]] = speakers.get(m["from"], 0) + 1
    return {
        "messages": messages,
        "channels": channels(messages),
        "speakers": sorted(
            ({"name": k, "count": v} for k, v in speakers.items()),
            key=lambda s: s["count"], reverse=True)[:20],
        "total": len(messages),
        "source": str(_mail_dir(Path(home))),
        "read_only": True,
    }
