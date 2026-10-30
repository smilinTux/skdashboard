"""Keep a fresh SKDashboard bearer on disk for an agent (e.g. ATLAS).

Agents read the control plane through ``skdashboard-control-plane-mcp``, which
takes a short-lived bearer file. Access tokens live five minutes, so a timer
calls :func:`refresh` every few minutes:

1. If a refresh token is on file, exchange it (rotates; the family lasts 8h).
2. Otherwise, or if that fails, log in: the agent signs a CapAuth challenge with
   its own key, and the code is exchanged at the token endpoint as the node's
   confidential ``skdashboard`` client.

The bearer is written atomically, mode 0600, to
``$XDG_RUNTIME_DIR/skdashboard-<name>.cap``. The refresh token stays in the
agent's 0700 state directory and never enters logs or the bearer file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

SCOPE = "openid skdashboard.read skdashboard.events.read"


def agent_dir(state_dir: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
        raise ValueError("agent name must be lowercase letters, digits, - or _")
    return state_dir / "agents" / name


def bearer_path(name: str) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / f"skdashboard-{name}.cap"


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _sign(gnupg: Path, passphrase: Path, fingerprint: str, payload: str) -> str:
    result = subprocess.run(
        [
            "gpg", "--homedir", str(gnupg), "--batch", "--pinentry-mode", "loopback",
            "--passphrase-file", str(passphrase), "-u", fingerprint, "--armor", "--detach-sign",
        ],
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"agent key {fingerprint[-8:]} could not sign the CapAuth challenge")
    return result.stdout.decode("ascii")


def _login_code(client: httpx.Client, cfg, agent: dict, adir: Path, verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    page = client.get(
        f"{cfg.issuer}/oidc/authorize",
        params={
            "response_type": "code",
            "client_id": "skdashboard",
            "redirect_uri": f"{cfg.dashboard_url}/auth/callback",
            "scope": SCOPE,
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge.decode("ascii"),
            "code_challenge_method": "S256",
        },
    )
    page.raise_for_status()
    match = re.search(r'REQUEST_ID = "([^"]+)"', page.text)
    if not match:
        raise RuntimeError("login server did not return a sign-in request")
    fingerprint = agent["fingerprint"]
    nonce = client.post(
        f"{cfg.issuer}/capauth/v1/challenge",
        json={
            "capauth_version": "1.0",
            "fingerprint": fingerprint,
            "client_nonce": base64.b64encode(secrets.token_bytes(16)).decode("ascii"),
        },
    )
    nonce.raise_for_status()
    ch = nonce.json()
    payload = "\n".join(
        [
            "CAPAUTH_NONCE_V1",
            f"nonce={ch['nonce']}",
            f"client_nonce={ch['client_nonce_echo']}",
            f"timestamp={ch['timestamp']}",
            f"service={ch['service']}",
            f"expires={ch['expires']}",
        ]
    )
    signature = _sign(adir / "gnupg", adir / "passphrase", fingerprint, payload)
    done = client.post(
        f"{cfg.issuer}/oidc/complete",
        json={
            "request_id": match.group(1),
            "fingerprint": fingerprint,
            "nonce": ch["nonce"],
            "nonce_signature": signature,
        },
    )
    if not done.is_success:
        raise RuntimeError(f"CapAuth refused the agent login ({done.status_code})")
    code = parse_qs(urlsplit(done.json()["redirect_to"]).query).get("code", [""])[0]
    if not code:
        raise RuntimeError("login server returned no authorization code")
    return code


def refresh(state_dir: Path, cfg, name: str, client_secret: str, *, http=None) -> Path:
    """Write a fresh bearer for ``name`` and return its path."""

    adir = agent_dir(state_dir, name)
    agent = json.loads((adir / "agent.json").read_text(encoding="utf-8"))
    refresh_file = adir / "refresh-token"
    client = http or httpx.Client(timeout=30, follow_redirects=False)
    token_url = f"{cfg.issuer}/oidc/token"
    auth = {"client_id": "skdashboard", "client_secret": client_secret}
    tokens = None
    if refresh_file.exists():
        response = client.post(
            token_url,
            data={"grant_type": "refresh_token", "refresh_token": refresh_file.read_text().strip(), **auth},
        )
        if response.is_success:
            tokens = response.json()
    if tokens is None:
        verifier = secrets.token_urlsafe(48)
        code = _login_code(client, cfg, agent, adir, verifier)
        response = client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{cfg.dashboard_url}/auth/callback",
                "code_verifier": verifier,
                **auth,
            },
        )
        if not response.is_success:
            raise RuntimeError(f"token exchange failed ({response.status_code})")
        tokens = response.json()
    if tokens.get("refresh_token"):
        _write_private(refresh_file, tokens["refresh_token"])
    target = bearer_path(name)
    _write_private(target, tokens["access_token"])
    return target
