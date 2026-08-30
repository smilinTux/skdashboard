"""Fail-closed bearer boundary for the same-origin session runtime."""

from __future__ import annotations


class SessionOnlyCapabilityAuthorizer:
    """Reject direct bearer authorization before any policy backend is read."""

    __slots__ = ()

    def authorize_with_receipt(self, *_args, **_kwargs):
        raise PermissionError("direct browser bearer authorization is disabled")


_SESSION_ONLY_AUTHORIZER = SessionOnlyCapabilityAuthorizer()


def build() -> SessionOnlyCapabilityAuthorizer:
    """Return the immutable production session-only authorization boundary."""

    return _SESSION_ONLY_AUTHORIZER


__all__ = ["SessionOnlyCapabilityAuthorizer", "build"]
