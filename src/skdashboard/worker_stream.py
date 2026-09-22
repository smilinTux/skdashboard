"""Merge bounded worker-activity projections from the five chi hosts."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

HOSTS = ("chiap01", "chiap02", "chiap03", "chiap04", "chiap08")
PROJECTED_FIELDS = frozenset({"ts", "host", "card", "role", "tool", "preview", "error"})
REQUIRED_PROJECTED_FIELDS = frozenset({"ts", "host", "card", "role", "tool", "preview"})
MAX_ROW_BYTES = 4_096
MAX_SNAPSHOT_BYTES = 64 * 1_024
SNAPSHOT_TIMEOUT_SECONDS = 10
FOLLOWER_STOP_SECONDS = 2
HEARTBEAT_SECONDS = 15
REMOTE_PROJECTOR = "~/work/skcapstone/scripts/fleet/skfleet-worker-stream.py"
CARD_RE = re.compile(r"[0-9a-f]{8}")

Spawn = Callable[..., Awaitable[asyncio.subprocess.Process]]


def _command(host: str, *, follow: bool) -> tuple[str, ...]:
    command = (
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "--",
        host,
        "python3",
        REMOTE_PROJECTOR,
    )
    return (*command, "--follow") if follow else command


def validate_card(card: str) -> None:
    if CARD_RE.fullmatch(card) is None:
        raise ValueError("card must be eight lowercase hexadecimal characters")


async def _snapshot_state(host: str, card: str, spawn: Spawn) -> dict[str, Any]:
    try:
        process = await spawn(
            *_command(host, follow=False),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(), timeout=SNAPSHOT_TIMEOUT_SECONDS
        )
    except OSError:
        return _state(host, card, "no-stream", "host-unavailable")
    except asyncio.TimeoutError:
        await _stop(process)
        return _state(host, card, "no-stream", "host-unavailable")
    if process.returncode != 0 or len(stdout) > MAX_SNAPSHOT_BYTES:
        return _state(host, card, "no-stream", "host-unavailable")
    for line in stdout.splitlines():
        row = _json_object(line)
        if row is None or row.get("host") != host or row.get("card") != card:
            continue
        if isinstance(row.get("session"), str) and row["session"]:
            return _state(host, card, "streaming", "current-session")
        if row.get("note") == "no session file for this run":
            return _state(
                host,
                card,
                "no-stream",
                "no-session-file-for-current-run",
            )
    return _state(host, card, "no-stream", "worker-not-active")


def _state(host: str, card: str, state: str, reason: str) -> dict[str, Any]:
    return {"type": "state", "host": host, "card": card, "state": state, "reason": reason}


def _json_object(line: bytes) -> dict[str, Any] | None:
    if not line or len(line) > MAX_ROW_BYTES:
        return None
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _activity(line: bytes, *, host: str, card: str) -> dict[str, Any] | None:
    row = _json_object(line)
    if (
        row is None
        or row.get("host") != host
        or row.get("card") != card
        or not REQUIRED_PROJECTED_FIELDS.issubset(row)
        or not set(row).issubset(PROJECTED_FIELDS)
    ):
        return None
    return {"type": "activity", **row}


async def _read_follower(
    host: str,
    card: str,
    process: asyncio.subprocess.Process,
    queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    assert process.stdout is not None
    try:
        while line := await process.stdout.readline():
            event = _activity(line, host=host, card=card)
            if event is not None:
                await queue.put(event)
    except (OSError, ValueError):
        pass
    finally:
        await queue.put(None)


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=FOLLOWER_STOP_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def stream(
    card: str,
    *,
    spawn: Spawn = asyncio.create_subprocess_exec,
) -> AsyncIterator[dict[str, Any] | None]:
    """Yield host states and live projected rows for one card.

    Each producer is first queried in snapshot mode so a current worker with
    no current-run session is explicit. Followers then start with ``--follow``
    and without ``--from-start``; the canonical producer consequently opens
    every session at EOF and emits only events appended after attachment.
    ``None`` is a transport heartbeat for SSE callers.
    """
    validate_card(card)
    states = await asyncio.gather(*(_snapshot_state(host, card, spawn) for host in HOSTS))
    for state in states:
        yield state

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=100)
    processes: list[asyncio.subprocess.Process] = []
    readers: list[asyncio.Task[None]] = []
    ended = 0
    try:
        for host in HOSTS:
            try:
                process = await spawn(
                    *_command(host, follow=True),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=MAX_ROW_BYTES + 1,
                )
            except OSError:
                ended += 1
                yield _state(host, card, "no-stream", "follower-unavailable")
                continue
            processes.append(process)
            readers.append(asyncio.create_task(_read_follower(host, card, process, queue)))

        while ended < len(HOSTS):
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield None
                continue
            if event is None:
                ended += 1
            else:
                yield event
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*(_stop(process) for process in processes), return_exceptions=True)
        await asyncio.gather(*readers, return_exceptions=True)


async def collect(card: str, *, spawn: Spawn = asyncio.create_subprocess_exec) -> list[dict]:
    """Collect a finite stream, primarily for bounded source qualification."""
    return [event async for event in stream(card, spawn=spawn) if event is not None]
