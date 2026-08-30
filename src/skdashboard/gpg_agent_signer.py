"""Fail-closed CapAuth signing through an already enrolled local gpg-agent."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


class GPGAgentCredentialSigner:
    """Sign bytes with one exact host-local key without handling key material."""

    __slots__ = ("_executable", "_fingerprint", "_home", "_passphrase_file", "_timeout")

    def __init__(
        self,
        *,
        issuer_fingerprint: str,
        gnupg_home: Path,
        passphrase_file: Path,
        executable: Path = Path("/usr/bin/gpg"),
        timeout_seconds: int = 15,
    ) -> None:
        fingerprint = issuer_fingerprint.strip().upper()
        if len(fingerprint) not in {40, 64} or any(
            character not in "0123456789ABCDEF" for character in fingerprint
        ):
            raise ValueError("issuer fingerprint must be full uppercase hexadecimal")
        if (
            not executable.is_absolute()
            or not gnupg_home.is_absolute()
            or not passphrase_file.is_absolute()
        ):
            raise ValueError("gpg paths must be absolute")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("gpg timeout must be between 1 and 30 seconds")
        self._fingerprint = fingerprint
        self._home = gnupg_home
        self._passphrase_file = passphrase_file
        self._executable = executable
        self._timeout = timeout_seconds

    @property
    def issuer_fingerprint(self) -> str:
        return self._fingerprint

    def sign(self, payload_bytes: bytes) -> str:
        if not isinstance(payload_bytes, bytes) or not payload_bytes:
            raise ValueError("a nonempty byte payload is required")
        executable = os.stat(self._executable, follow_symlinks=False)
        home = os.stat(self._home, follow_symlinks=False)
        passphrase = os.stat(self._passphrase_file, follow_symlinks=False)
        if (
            not stat.S_ISREG(executable.st_mode)
            or executable.st_mode & 0o022
            or not stat.S_ISDIR(home.st_mode)
            or home.st_uid != os.getuid()
            or home.st_mode & 0o077
            or not stat.S_ISREG(passphrase.st_mode)
            or passphrase.st_uid != os.getuid()
            or stat.S_IMODE(passphrase.st_mode) != 0o600
            or passphrase.st_nlink != 1
            or passphrase.st_size == 0
        ):
            raise PermissionError("gpg signer custody boundary is invalid")
        command = (
            str(self._executable),
            "--homedir",
            str(self._home),
            "--batch",
            "--yes",
            "--armor",
            "--detach-sign",
            "--local-user",
            self._fingerprint,
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(self._passphrase_file),
            "--output",
            "-",
        )
        try:
            result = subprocess.run(
                command,
                input=payload_bytes,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("host-local signer is unavailable") from None
        signature = result.stdout
        if result.returncode != 0 or not signature.startswith(b"-----BEGIN PGP SIGNATURE-----"):
            raise RuntimeError("host-local signer is unavailable")
        try:
            rendered = signature.decode("ascii")
        except UnicodeDecodeError:
            raise RuntimeError("host-local signer returned an invalid signature") from None
        if not rendered.rstrip().endswith("-----END PGP SIGNATURE-----"):
            raise RuntimeError("host-local signer returned an invalid signature")
        return rendered


__all__ = ["GPGAgentCredentialSigner"]
