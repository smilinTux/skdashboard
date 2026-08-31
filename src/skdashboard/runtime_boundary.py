"""Validated process-level browser and bind boundary."""

from __future__ import annotations

import os
from ipaddress import ip_address
from urllib.parse import urlsplit

DEFAULT_BROWSER_ORIGINS = frozenset(
    {"https://10.0.0.139:7778", "https://chiap08.tail204f0c.ts.net:7778"}
)
DEFAULT_BIND_HOSTS = frozenset({"127.0.0.1", "10.0.0.139", "100.81.238.58"})


def _values(environ, name: str, default: frozenset[str]) -> frozenset[str]:
    raw = environ.get(name)
    if raw is None:
        return default
    values = raw.split(",")
    if (
        not 1 <= len(values) <= 8
        or any(not value or value != value.strip() for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError(f"{name} must contain unique comma-separated exact values")
    return frozenset(values)


def load_runtime_boundary(environ=None) -> tuple[frozenset[str], frozenset[str]]:
    """Load one exact fail-closed runtime boundary from process environment."""

    values = os.environ if environ is None else environ
    origins = _values(
        values,
        "SKDASHBOARD_ALLOWED_BROWSER_ORIGINS",
        DEFAULT_BROWSER_ORIGINS,
    )
    hosts = _values(values, "SKDASHBOARD_ALLOWED_BIND_HOSTS", DEFAULT_BIND_HOSTS)
    for host in hosts:
        address = ip_address(host)
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise ValueError("bind hosts must be named IPv4 addresses")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path
            or parsed.hostname is None
            or parsed.port is None
        ):
            raise ValueError("browser origins must be exact named HTTPS bind origins")
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            if "." not in parsed.hostname:
                raise ValueError("browser origins must use an exact FQDN or named bind address")
        else:
            if address.version != 4 or parsed.hostname not in hosts:
                raise ValueError("IP browser origins must use named bind addresses")
    return origins, hosts


ALLOWED_BROWSER_ORIGINS, ALLOWED_BIND_HOSTS = load_runtime_boundary()


__all__ = [
    "ALLOWED_BIND_HOSTS",
    "ALLOWED_BROWSER_ORIGINS",
    "DEFAULT_BIND_HOSTS",
    "DEFAULT_BROWSER_ORIGINS",
    "load_runtime_boundary",
]
