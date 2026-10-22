"""cloud9 ⇄ skcapstone — optional integration adapter.

cloud9 runs fully standalone.  When the ``skcapstone`` package is
installed (and the operator has not forced standalone mode with
``SK_STANDALONE=1``), this adapter routes FEB/OOF events through skcapstone's
shared **sk-alert** bus and registers the rehydration/FEB-state check with
the fleet **skscheduler**, so the whole sk* mesh sees the emotional continuity
stream.  When skcapstone is absent, every call degrades to cloud9's native
behaviour (structured logging + the systemd ``cloud9-daemon.timer`` / launchd
plist cadence).

This is the *default-on-by-presence* pattern from
``skcapstone/docs/ADR-optional-integration-backbone.md`` — nothing here is a
hard dependency; ``skcapstone`` lives in the optional ``[skcapstone]`` extra.

Public API:
    is_present()                       -> bool
    alert(event, payload, level)       -> bool   (True iff sent via sk-alert)
    ensure_schedule(interval_hours)    -> bool   (True iff registered with skscheduler)
    unregister_schedule()              -> bool
    register_self(pid_file)            -> bool

Topic convention: ``cloud9.<severity>`` (severity ∈ info|warn|error|critical).
The semantic *event* name is carried in the payload ``event`` field — not the
topic suffix — so ``skcapstone alerts``' ``*.error``/``*.critical``/``*.warn``
wildcards match by severity while detail is preserved.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("cloud9.integration")

#: This service's name — used as the alert topic prefix and registry key.
SERVICE = "cloud9"

#: Fleet-scheduler job name for the rehydration/FEB-state health check.
REHYDRATION_JOB = "cloud9_rehydration_check"

# Optional import — never a hard dependency.
try:
    from skcapstone import sdk as _sdk
except Exception:  # ImportError, or a broken partial install
    _sdk = None  # type: ignore[assignment]

#: severity → logging method name (native fallback)
_LOG_METHOD = {
    "info": "info",
    "warn": "warning",
    "error": "error",
    "critical": "critical",
}
_NOTIFY_LEVELS = frozenset({"warn", "error", "critical"})


def is_present() -> bool:
    """Return whether skcapstone integration should be used from this process.

    ``True`` only when the package imported, the operator has not set
    ``SK_STANDALONE``, and the SDK reports itself available.  Any failure is
    treated as "not present" so callers transparently use their native path.
    """
    if os.environ.get("SK_STANDALONE"):
        return False
    if _sdk is None:
        return False
    try:
        return bool(_sdk.is_available())
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("skcapstone present-check failed: %s", exc)
        return False


def alert(event: str, payload: dict[str, Any], level: str = "info") -> bool:
    """Emit an alert: via skcapstone sk-alert when present, else local log.

    The published topic follows the ecosystem convention ``cloud9.<severity>``
    (so ``skcapstone alerts`` — which subscribes to ``*.error`` / ``*.critical``
    / ``*.warn`` — surfaces it).  The semantic *event* name is carried in the
    payload's ``event`` field rather than the topic, so routing stays
    severity-based while detail is preserved.

    Typical events: ``"feb_load_failed"``, ``"rehydration_failed"``,
    ``"oof_triggered"``, ``"cloud9_achieved"``, ``"feb_parse_error"``.

    Args:
        event: Semantic event name.  Stored in the payload as ``event``.
        payload: JSON-serialisable event body.
        level: ``info | warn | error | critical``.

    Returns:
        ``True`` if published to the shared bus, ``False`` if it fell back to
        local logging (which always also happens at the matching level).
    """
    body = {"event": event, **dict(payload)}
    if is_present():
        try:
            return bool(
                _sdk.alert(
                    f"{SERVICE}.{level}",
                    body,
                    level=level,
                    notify=level in _NOTIFY_LEVELS,
                )
            )
        except Exception as exc:
            logger.warning("sk-alert publish failed, logging locally: %s", exc)

    # native fallback — structured log at the matching level
    method = getattr(logger, _LOG_METHOD.get(level, "info"))
    method("[%s.%s] %s", SERVICE, level, body)
    return False


def ensure_schedule(interval_hours: float = 6.0) -> bool:
    """Register the rehydration/FEB-state check with the fleet scheduler.

    Writes a ``jobs.d/cloud9_rehydration_check.yaml`` drop-in that runs
    ``cloud9 oof <latest_feb>`` every *interval_hours* to verify that the
    emotional continuity state is healthy and the latest FEB is loadable.
    Idempotent — safe to call on every startup.

    When skcapstone is absent the native ``cloud9-daemon.timer`` (systemd) or
    the launchd plist remain the cadence mechanism.

    Args:
        interval_hours: Check cadence in hours (default 6h).

    Returns:
        ``True`` if registered with skscheduler; ``False`` when skcapstone is
        absent and the caller should rely on its native timer.
    """
    if not is_present():
        return False
    try:
        _sdk.register_job(
            {
                "name": REHYDRATION_JOB,
                "type": "shell",
                "command": "cloud9 validate --latest",
                "every": f"{int(interval_hours * 3600)}s",
                "timeout": 120,
                "notify": "on_failure",
                "notify_level": "error",
            }
        )
        logger.info(
            "Registered '%s' with skcapstone scheduler (every %.1fh).",
            REHYDRATION_JOB,
            interval_hours,
        )
        return True
    except Exception as exc:
        logger.warning("ensure_schedule failed (using native): %s", exc)
        return False


def unregister_schedule() -> bool:
    """Remove the rehydration-check drop-in from the fleet scheduler."""
    if _sdk is None:
        return False
    try:
        return bool(_sdk.unregister_job(REHYDRATION_JOB))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("unregister_schedule failed: %s", exc)
        return False


def register_self(pid_file: Optional[str] = None) -> bool:
    """Advertise cloud9 to skcapstone's discovery registry, if present.

    Args:
        pid_file: Optional pid-file path used as a liveness signal.

    Returns:
        ``True`` if registered, ``False`` otherwise.
    """
    if not is_present():
        return False
    try:
        _sdk.register_service(
            SERVICE,
            pid_file=pid_file or str(Path("~/.cloud9/daemon.pid").expanduser()),
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("register_self failed: %s", exc)
        return False
