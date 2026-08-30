import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from skdashboard.gpg_agent_signer import GPGAgentCredentialSigner

FINGERPRINT = "DCE38ED7BC9D95D724B5FE7FECF9D6A423EC83F5"
SIGNATURE = b"-----BEGIN PGP SIGNATURE-----\nfixture\n-----END PGP SIGNATURE-----\n"


def signer(tmp_path: Path) -> GPGAgentCredentialSigner:
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    executable = tmp_path / "gpg"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    return GPGAgentCredentialSigner(
        issuer_fingerprint=FINGERPRINT,
        gnupg_home=home,
        executable=executable,
    )


def test_signer_uses_exact_agent_key_without_passphrase_or_shell(tmp_path, monkeypatch) -> None:
    adapter = signer(tmp_path)
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0, stdout=SIGNATURE, stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert adapter.sign(b"payload") == SIGNATURE.decode("ascii")
    command, options = calls[0]
    assert command[-4:] == ("--local-user", FINGERPRINT, "--output", "-")
    assert "--passphrase" not in command
    assert "--pinentry-mode" not in command
    assert options == {
        "input": b"payload",
        "capture_output": True,
        "timeout": 15,
        "check": False,
    }


@pytest.mark.parametrize(
    "fingerprint",
    ["", "DCE38", "g" * 40, FINGERPRINT.lower() + "00"],
)
def test_signer_rejects_invalid_fingerprint(tmp_path, fingerprint) -> None:
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        GPGAgentCredentialSigner(
            issuer_fingerprint=fingerprint,
            gnupg_home=home,
        )


def test_signer_fails_closed_without_copying_subprocess_output(tmp_path, monkeypatch) -> None:
    adapter = signer(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2,
            stdout=b"",
            stderr=b"secret operator context",
        ),
    )
    with pytest.raises(RuntimeError, match="host-local signer is unavailable") as error:
        adapter.sign(b"payload")
    assert "secret" not in str(error.value)


def test_signer_rejects_unsafe_home_before_invoking_gpg(tmp_path, monkeypatch) -> None:
    adapter = signer(tmp_path)
    adapter._home.chmod(0o755)
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(PermissionError, match="custody boundary"):
        adapter.sign(b"payload")
    assert called == []


def test_signer_rejects_empty_payload(tmp_path) -> None:
    with pytest.raises(ValueError, match="nonempty"):
        signer(tmp_path).sign(b"")
