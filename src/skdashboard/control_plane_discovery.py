"""Signed SKWorld discovery profile for the versioned control plane."""

from __future__ import annotations

import re
from collections.abc import Mapping

CONTRACT_VERSION = "1.1.0"
SCHEMA_VERSION = "1.3"
BASE_PATH = "/api/v1"
CAPABILITIES = {
    "preview": "skdashboard.actions.preview",
    "authorize": "skdashboard.actions.authorize",
}
RESOURCE_PATHS = {
    "health": "/api/v1/health",
    "overview": "/api/v1/overview",
    "board": "/api/v1/board/summary",
    "fleet": "/api/v1/fleet/summary",
    "economy": "/api/v1/economy/summary",
    "report": "/api/v1/reports/{snapshot_id}",
}
_PATH = re.compile(r"^/(?!/)(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9._~!$&'()*+,;=:@%{}/-]+$")


def control_plane_profile() -> dict:
    """Return the closed origin-relative discovery facet."""
    return {
        "contractVersion": CONTRACT_VERSION,
        "basePath": BASE_PATH,
        "openapi": "/api/v1/openapi.json",
        "events": {
            "path": "/api/v1/events",
            "mediaType": "text/event-stream",
            "resume": "cursor",
        },
        "resources": dict(RESOURCE_PATHS),
        "commands": {
            "preview": "/api/v1/actions/preview",
            "authorize": "/api/v1/action-previews/{preview_id}/authorize",
            "receipt": "/api/v1/action-receipts/{receipt_id}",
            "capabilities": dict(CAPABILITIES),
        },
        "compatibility": {
            "minimumClientContract": CONTRACT_VERSION,
            "unsupportedVersion": "fail_closed",
        },
        "deprecation": {
            "policy": "/api/v1/deprecation-policy",
            "sunsetHeaderRequired": True,
        },
    }


def validate_control_plane_profile(manifest: Mapping[str, object]) -> None:
    """Fail closed unless a manifest carries the exact supported profile."""
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("unsupported SKWorld manifest schema")
    profile = manifest.get("controlPlane")
    if profile != control_plane_profile():
        raise ValueError("unsupported control-plane discovery profile")
    for value in _paths(profile):
        if not _PATH.fullmatch(value):
            raise ValueError("control-plane discovery path is not origin-relative")


def _paths(profile: object) -> list[str]:
    if not isinstance(profile, Mapping):
        return []
    values = [profile.get("basePath"), profile.get("openapi")]
    events = profile.get("events")
    if isinstance(events, Mapping):
        values.append(events.get("path"))
    resources = profile.get("resources")
    if isinstance(resources, Mapping):
        values.extend(resources.values())
    commands = profile.get("commands")
    if isinstance(commands, Mapping):
        values.extend(commands.get(key) for key in ("preview", "authorize", "receipt"))
    deprecation = profile.get("deprecation")
    if isinstance(deprecation, Mapping):
        values.append(deprecation.get("policy"))
    return [value for value in values if isinstance(value, str)]


__all__ = [
    "BASE_PATH",
    "CAPABILITIES",
    "CONTRACT_VERSION",
    "RESOURCE_PATHS",
    "SCHEMA_VERSION",
    "control_plane_profile",
    "validate_control_plane_profile",
]
