"""Bounded runtime build identity shared by SKDashboard app factories."""

from __future__ import annotations

import os
from importlib import metadata

from starlette.responses import JSONResponse

BUILD_INFO_SCHEMA = "skdashboard.build-info/v1"
BUILD_VALUE_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
)


def _bounded_build_value(value: str | None) -> str:
    candidate = (value or "").strip()
    if (
        not candidate
        or len(candidate) > 128
        or any(character not in BUILD_VALUE_CHARACTERS for character in candidate)
    ):
        return "unavailable"
    return candidate


def _build_information() -> dict[str, str]:
    try:
        package_version = _bounded_build_value(metadata.version("skdashboard"))
    except metadata.PackageNotFoundError:
        package_version = "unavailable"
    source_commit = _bounded_build_value(os.environ.get("SKDASHBOARD_SOURCE_COMMIT"))
    if source_commit != "unavailable":
        if len(source_commit) < 7 or any(
            character not in "0123456789abcdefABCDEF" for character in source_commit
        ):
            source_commit = "unavailable"
        else:
            source_commit = source_commit.lower()[:12]
    return {
        "schema_version": BUILD_INFO_SCHEMA,
        "application": "SKDashboard",
        "package_version": package_version,
        "source_commit": source_commit,
        "release_identifier": _bounded_build_value(
            os.environ.get("SKDASHBOARD_RELEASE_IDENTIFIER")
        ),
    }


async def build_information(_request):
    return JSONResponse(_build_information(), headers={"Cache-Control": "no-store"})
