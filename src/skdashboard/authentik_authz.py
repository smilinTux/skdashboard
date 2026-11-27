"""Authorize dashboard API calls from an Authentik access token's groups."""

from __future__ import annotations

import httpx
import jwt

# Deny by default: a capability not named here is admin-only.
READ_CAPABILITIES = frozenset(
    {"skdashboard.read", "skdashboard.events.read", "skdashboard.reports.read"}
)
MAX_TOKEN_LIFETIME = 300


class AuthentikAuthorizer:
    """Check one short-lived Authentik JWT, then map its groups to capabilities."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        admin_groups,
        user_groups=(),
        *,
        jwk_client=None,
    ) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self.admin_groups = frozenset(admin_groups)
        self.user_groups = frozenset(user_groups)
        self._jwk_client = jwk_client

    def _signing_key(self, token: str):
        if self._jwk_client is None:
            response = httpx.get(f"{self.issuer}.well-known/openid-configuration", timeout=10.0)
            response.raise_for_status()
            metadata = response.json()
            if metadata.get("issuer") != self.issuer:
                raise ValueError("issuer mismatch")
            self._jwk_client = jwt.PyJWKClient(metadata["jwks_uri"], lifespan=3600, timeout=10)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def __call__(self, bearer: str, capability: str, target: str) -> bool:
        try:
            claims = jwt.decode(
                bearer,
                self._signing_key(bearer),
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            if claims["exp"] - claims["iat"] > MAX_TOKEN_LIFETIME:
                return False
            groups = claims.get("groups")
            if not isinstance(groups, list):
                return False
        except Exception:
            return False
        names = {group for group in groups if isinstance(group, str)}
        if names & self.admin_groups:
            return capability.startswith("skdashboard.")
        return bool(names & self.user_groups) and capability in READ_CAPABILITIES
