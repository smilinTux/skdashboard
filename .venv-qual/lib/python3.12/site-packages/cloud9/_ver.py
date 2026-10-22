"""Resolve the package version.

Separate module because ``__version__`` must be assigned before the submodule
imports in ``__init__``, and a function definition in that position makes every
import after it an E402. A dunder assignment there is fine; a ``def`` is not.

The version used to be hardcoded in pyproject.toml AND here, and the two had
already drifted apart. The git tag is the single source of truth now.
"""

from __future__ import annotations


def detect_version() -> str:
    """Installed version, else the build-time one, else an obvious fallback.

    Both lookups fail with ImportError and nothing else worth swallowing:
    ``PackageNotFoundError`` subclasses ``ModuleNotFoundError`` which subclasses
    ``ImportError``, and a missing ``_version`` module raises it directly. Caught
    narrowly on purpose, so a genuine bug in here surfaces instead of silently
    degrading to the fallback string.

    That fallback is deliberately not a plausible number: a wrong-but-believable
    version is what caused the original outage.
    """
    try:
        from importlib.metadata import version

        return version("cloud9")
    except ImportError:  # not installed, or running from a source tree
        pass
    try:
        from ._version import version as scm_version  # written by setuptools-scm

        return scm_version
    except ImportError:
        return "0.0.0+unknown"
