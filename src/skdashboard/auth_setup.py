"""Install and manage CapAuth login for SKDashboard on one node.

``skdashboard-auth`` turns any node into a self-contained, login-protected
SKDashboard in one command, and lets the owner grant CapAuth identities access:

    skdashboard-auth setup              # login server + HTTPS dashboard + cert renewal
    skdashboard-auth grant              # grant your own CapAuth identity
    skdashboard-auth grant --pubkey k.asc   # grant another person's CapAuth key
    skdashboard-auth revoke <fingerprint>
    skdashboard-auth status
    skdashboard-auth enable|disable     # require login on the local :7778 dashboard too

Trust layout (all node-local, never written into the Syncthing-replicated agent home):

* The login server (CapAuth OIDC IdP) runs with its own dedicated PGP service key.
  That key signs only the five-minute session tokens and the node-local grants.
* A person logs in by signing a CapAuth challenge with their OWN CapAuth key; the
  login server only accepts fingerprints the owner granted.
* The dashboard verifies session tokens against a public-only keyring holding the
  service key, and checks grants in the login server's CapAuth home.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "skdashboard-auth"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
IDP_UNIT = "skdashboard-auth-idp.service"
DASHBOARD_UNIT = "skdashboard-auth-dashboard.service"
CERT_UNIT = "skdashboard-auth-cert.service"
CERT_TIMER = "skdashboard-auth-cert.timer"
LOCAL_DASHBOARD_UNIT = "skcapstone-dashboard.service"
LOCAL_DROPIN = "skdashboard-auth.conf"
DASHBOARD_SCOPES = ["skdashboard.read", "skdashboard.events.read"]
GRANT_TTL_HOURS = 24 * 365


@dataclass
class Config:
    hostname: str
    bind: str
    dashboard_port: int = 7779
    idp_port: int = 8421
    agent_home: str = str(Path.home() / ".skcapstone")
    service_fingerprint: str = ""

    @property
    def dashboard_url(self) -> str:
        return f"https://{self.hostname}:{self.dashboard_port}"

    @property
    def issuer(self) -> str:
        return f"https://{self.hostname}:{self.idp_port}"


class Layout:
    def __init__(self, root: Path):
        self.root = root
        self.config = root / "config.json"
        self.capauth_home = root / "capauth-home"
        self.gnupg = root / "gnupg"
        self.verify_gnupg = root / "verify-gnupg"
        self.secrets = root / "secrets"
        self.tls = root / "tls"
        self.cert = self.tls / "fullchain.pem"
        self.key = self.tls / "privkey.pem"
        self.passphrase = self.secrets / "idp.passphrase"
        self.idp_env = self.secrets / "idp.env"
        self.client_secret = self.secrets / "oidc-client.secret"
        self.session_key = self.secrets / "session.key"
        self.session_db = root / "dashboard" / "session.db"
        self.clients = self.capauth_home / "oidc" / "clients.json"
        self.keys_db = self.capauth_home / "service" / "keys.db"

    def dirs(self):
        return [
            self.root,
            self.capauth_home / "identity",
            self.capauth_home / "service",
            self.capauth_home / "oidc",
            self.capauth_home / "passkeys",
            self.capauth_home / "data",
            self.gnupg,
            self.verify_gnupg,
            self.secrets,
            self.tls,
            self.session_db.parent,
        ]


def _say(message: str) -> None:
    print(f"  {message}")


def _run(cmd: list[str], *, env=None, check=True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, check=check, text=True, capture_output=True, **kwargs)


def _write_secret(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _ensure_secret(path: Path, factory) -> None:
    if not path.exists() or path.stat().st_size == 0:
        _write_secret(path, factory())
    os.chmod(path, 0o600)


def _tailscale_self() -> tuple[str, str] | None:
    if not shutil.which("tailscale"):
        return None
    try:
        status = json.loads(_run(["tailscale", "status", "--json", "--self"]).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    me = status.get("Self") or {}
    name = (me.get("DNSName") or "").rstrip(".")
    ipv4 = next((ip for ip in me.get("TailscaleIPs") or [] if "." in ip), "")
    return (name, ipv4) if name and ipv4 else None


def load_config(layout: Layout) -> Config:
    if not layout.config.exists():
        sys.exit(f"not set up yet: run `skdashboard-auth setup` ({layout.config} missing)")
    return Config(**json.loads(layout.config.read_text(encoding="utf-8")))


def _gpg(layout_home: Path, *args: str, passphrase: Path | None = None, input_text=None):
    cmd = ["gpg", "--homedir", str(layout_home), "--batch"]
    if passphrase is not None:
        cmd += ["--pinentry-mode", "loopback", "--passphrase-file", str(passphrase)]
    return _run(cmd + list(args), input=input_text)


def ensure_service_key(layout: Layout, cfg: Config) -> str:
    identity = layout.capauth_home / "identity" / "identity.json"
    if identity.exists():
        fingerprint = json.loads(identity.read_text(encoding="utf-8"))["fingerprint"]
        _say(f"service key present ({fingerprint[-16:]})")
        return fingerprint
    uid = f"SKDashboard login server ({cfg.hostname}) <skdashboard-auth@{cfg.hostname}>"
    _gpg(layout.gnupg, "--quick-gen-key", uid, "ed25519", "sign", "never", passphrase=layout.passphrase)
    listing = _gpg(layout.gnupg, "--with-colons", "--list-secret-keys").stdout
    fingerprint = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
    identity.write_text(
        json.dumps(
            {
                "name": f"SKDashboard login server ({cfg.hostname})",
                "email": f"skdashboard-auth@{cfg.hostname}",
                "fingerprint": fingerprint,
                "capauth_managed": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _say(f"created service key {fingerprint}")
    return fingerprint


def ensure_verify_keyring(layout: Layout, fingerprint: str) -> None:
    armor = _gpg(layout.gnupg, "--armor", "--export", fingerprint).stdout
    _gpg(layout.verify_gnupg, "--import", input_text=armor)
    _say("dashboard verification keyring holds the service public key")


def ensure_tls(layout: Layout, cfg: Config, *, cert=None, key=None) -> None:
    if cert and key:
        shutil.copyfile(cert, layout.cert)
        shutil.copyfile(key, layout.key)
    else:
        if not shutil.which("tailscale"):
            sys.exit("no --tls-cert/--tls-key given and tailscale is not installed")
        _run(
            [
                "tailscale",
                "cert",
                "--cert-file",
                str(layout.cert),
                "--key-file",
                str(layout.key),
                cfg.hostname,
            ]
        )
    os.chmod(layout.key, 0o600)
    _say(f"TLS certificate ready for {cfg.hostname}")


def ensure_oidc(layout: Layout, cfg: Config) -> None:
    _ensure_secret(layout.passphrase, lambda: secrets.token_urlsafe(32))
    _ensure_secret(layout.client_secret, lambda: secrets.token_urlsafe(48))
    _ensure_secret(layout.idp_env, lambda: f"CAPAUTH_JWT_SECRET={secrets.token_hex(32)}\n")
    if not layout.session_key.exists():
        from cryptography.fernet import Fernet

        _write_secret(layout.session_key, Fernet.generate_key().decode("ascii"))
    client = {
        "client_id": "skdashboard",
        "client_secret": layout.client_secret.read_text(encoding="utf-8").strip(),
        "redirect_uris": [f"{cfg.dashboard_url}/auth/callback"],
        "name": "SKDashboard",
        "scopes": ["openid", *DASHBOARD_SCOPES],
    }
    _write_secret(layout.clients, json.dumps([client], indent=2))
    _say("OIDC client, session key and login-server secrets ready")


def render_units(layout: Layout, cfg: Config) -> dict[str, str]:
    home = layout.capauth_home
    idp = f"""[Unit]
Description=SKDashboard CapAuth login server ({cfg.issuer})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=CAPAUTH_HOME={home}
Environment=CAPAUTH_OIDC_ISSUER={cfg.issuer}
Environment=CAPAUTH_BASE_URL={cfg.issuer}
Environment=CAPAUTH_SERVICE_ID={cfg.hostname}
Environment=CAPAUTH_WEBAUTHN_RP_ID={cfg.hostname}
Environment=CAPAUTH_PASSKEY_DATA_DIR={home / "passkeys"}
Environment=CAPAUTH_OIDC_CLIENTS_FILE={layout.clients}
Environment=CAPAUTH_DB_PATH={layout.keys_db}
Environment=CAPAUTH_DATA_DIR={home / "data"}
Environment=CAPAUTH_OIDC_STATE_DB={home / "service" / "oidc_state.db"}
Environment=CAPAUTH_OIDC_SIGNING_KEY_PATH={home / "service" / "oidc_signing_key.pem"}
Environment=CAPAUTH_REQUIRE_APPROVAL=true
Environment=CAPAUTH_GPG_PASSPHRASE_FILE={layout.passphrase}
Environment=GNUPGHOME={layout.gnupg}
EnvironmentFile={layout.idp_env}
ExecStart={sys.executable} -m uvicorn capauth.service.app:app --host {cfg.bind} --port {cfg.idp_port} --ssl-certfile {layout.cert} --ssl-keyfile {layout.key}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
"""
    dashboard = f"""[Unit]
Description=SKDashboard with CapAuth login ({cfg.dashboard_url})
After=network-online.target {IDP_UNIT}
Wants=network-online.target {IDP_UNIT}

[Service]
Type=simple
Environment=GNUPGHOME={layout.verify_gnupg}
Environment=SKDASHBOARD_ALLOWED_BIND_HOSTS={cfg.bind}
Environment=SKDASHBOARD_ALLOWED_BROWSER_ORIGINS={cfg.dashboard_url}
ExecStart={sys.executable} -m skdashboard.read_only --home {cfg.agent_home} --capauth-home {home} --host {cfg.bind} --port {cfg.dashboard_port} --tls-certfile {layout.cert} --tls-keyfile {layout.key} --session-db {layout.session_db} --session-key-file {layout.session_key} --oidc-issuer {cfg.issuer} --oidc-redirect-uri {cfg.dashboard_url}/auth/callback --oidc-client-secret-file {layout.client_secret}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
"""
    cert = f"""[Unit]
Description=Renew the SKDashboard login TLS certificate
After=network-online.target

[Service]
Type=oneshot
ExecStart={sys.executable} -m skdashboard.auth_setup renew-cert --state-dir {layout.root}
"""
    timer = """[Unit]
Description=Weekly SKDashboard login TLS certificate renewal

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=1h

[Install]
WantedBy=timers.target
"""
    return {IDP_UNIT: idp, DASHBOARD_UNIT: dashboard, CERT_UNIT: cert, CERT_TIMER: timer}


def _systemctl(*args: str, check=True):
    return _run(["systemctl", "--user", *args], check=check)


def install_units(layout: Layout, cfg: Config, *, start: bool) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in render_units(layout, cfg).items():
        (UNIT_DIR / name).write_text(body, encoding="utf-8")
    _systemctl("daemon-reload")
    if start:
        _systemctl("enable", "--now", CERT_TIMER)
        _systemctl("enable", IDP_UNIT, DASHBOARD_UNIT)
        _systemctl("restart", IDP_UNIT, DASHBOARD_UNIT)
    _say("systemd user units installed" + (" and started" if start else ""))


def cmd_setup(args) -> None:
    layout = Layout(args.state_dir)
    detected = _tailscale_self()
    previous = Config(**json.loads(layout.config.read_text())) if layout.config.exists() else None
    hostname = args.hostname or (previous and previous.hostname) or (detected and detected[0])
    bind = args.bind or (previous and previous.bind) or (detected and detected[1])
    if not hostname or not bind:
        sys.exit("could not detect a Tailscale name; pass --hostname and --bind")
    cfg = Config(
        hostname=hostname,
        bind=bind,
        dashboard_port=args.dashboard_port,
        idp_port=args.idp_port,
        agent_home=str(args.agent_home),
    )
    print(f"SKDashboard login setup for {cfg.dashboard_url}")
    for directory in layout.dirs():
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    ensure_oidc(layout, cfg)
    cfg.service_fingerprint = ensure_service_key(layout, cfg)
    ensure_verify_keyring(layout, cfg.service_fingerprint)
    ensure_tls(layout, cfg, cert=args.tls_cert, key=args.tls_key)
    layout.config.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    install_units(layout, cfg, start=not args.no_start)
    print(f"\nDone. Grant yourself with `skdashboard-auth grant`, then open {cfg.dashboard_url}")


def _operator_pubkey(agent_home: Path) -> Path:
    for candidate in (
        agent_home / "capauth" / "identity" / "public.asc",
        Path.home() / ".capauth" / "identity" / "public.asc",
    ):
        if candidate.exists():
            return candidate
    sys.exit("no CapAuth identity found; pass --pubkey <armored public key file>")


def _service_env(layout: Layout) -> None:
    os.environ["GNUPGHOME"] = str(layout.gnupg)
    os.environ["CAPAUTH_GPG_PASSPHRASE_FILE"] = str(layout.passphrase)
    os.environ["CAPAUTH_HOME"] = str(layout.capauth_home)


def cmd_grant(args) -> None:
    layout = Layout(args.state_dir)
    cfg = load_config(layout)
    pubkey_file = args.pubkey or _operator_pubkey(Path(cfg.agent_home))
    armor = Path(pubkey_file).read_text(encoding="utf-8")
    from capauth.authentik.verifier import fingerprint_from_armor

    fingerprint = (fingerprint_from_armor(armor) or "").upper()
    if len(fingerprint) not in {40, 64}:
        sys.exit(f"could not read a PGP fingerprint from {pubkey_file}")
    _service_env(layout)
    from capauth.provisioning import provision_subject
    from capauth.service.keystore import KeyStore

    store = KeyStore(layout.keys_db)
    try:
        if store.get(fingerprint) is None:
            store.enroll(fingerprint, armor, approved=True)
        else:
            store.approve(fingerprint)
    finally:
        store.close()
    result = provision_subject(
        f"device:{fingerprint.lower()}",
        DASHBOARD_SCOPES,
        identity_class="operator",
        ttl_hours=GRANT_TTL_HOURS,
        approver=f"skdashboard-auth@{cfg.hostname}",
        base_dir=layout.capauth_home,
    )
    print(f"Granted {fingerprint} dashboard read access (subject {result['subject']}).")
    print(f"Log in at {cfg.dashboard_url} with that CapAuth key. Re-run grant to renew in a year.")


def cmd_revoke(args) -> None:
    layout = Layout(args.state_dir)
    load_config(layout)
    from capauth.service.keystore import KeyStore

    store = KeyStore(layout.keys_db)
    try:
        revoked = store.revoke(args.fingerprint.upper())
    finally:
        store.close()
    print("revoked" if revoked else "no such enrolled fingerprint")


def _local_dropin() -> Path:
    return UNIT_DIR / f"{LOCAL_DASHBOARD_UNIT}.d" / LOCAL_DROPIN


def set_local_auth(mode: str, cfg: Config | None) -> Path:
    dropin = _local_dropin()
    dropin.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by skdashboard-auth enable/disable.", "[Service]", f"Environment=SKDASHBOARD_AUTH={mode}"]
    if cfg is not None:
        lines.append(f"Environment=SKDASHBOARD_SECURE_URL={cfg.dashboard_url}")
    dropin.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dropin


def _toggle(args, mode: str) -> None:
    layout = Layout(args.state_dir)
    cfg = load_config(layout) if layout.config.exists() else None
    if mode == "on" and cfg is None:
        sys.exit("run `skdashboard-auth setup` first, or local login would have nowhere to go")
    dropin = set_local_auth(mode, cfg)
    _systemctl("daemon-reload")
    _systemctl("try-restart", LOCAL_DASHBOARD_UNIT, check=False)
    print(f"Local dashboard login {'required' if mode == 'on' else 'not required on localhost'} ({dropin}).")


def cmd_renew_cert(args) -> None:
    layout = Layout(args.state_dir)
    cfg = load_config(layout)
    if shutil.which("tailscale") and cfg.hostname.endswith(".ts.net"):
        ensure_tls(layout, cfg)
        _systemctl("try-restart", IDP_UNIT, DASHBOARD_UNIT, check=False)


def cmd_status(args) -> None:
    layout = Layout(args.state_dir)
    cfg = load_config(layout)
    print(f"Dashboard:    {cfg.dashboard_url}")
    print(f"Login server: {cfg.issuer}")
    print(f"Service key:  {cfg.service_fingerprint}")
    for unit in (IDP_UNIT, DASHBOARD_UNIT, CERT_TIMER, LOCAL_DASHBOARD_UNIT):
        state = _systemctl("is-active", unit, check=False).stdout.strip()
        print(f"  {unit:42s} {state}")
    dropin = _local_dropin()
    mode = "off (default)"
    if dropin.exists() and "SKDASHBOARD_AUTH=on" in dropin.read_text(encoding="utf-8"):
        mode = "on"
    print(f"Local :7778 login required: {mode}")
    from capauth.service.keystore import KeyStore

    store = KeyStore(layout.keys_db)
    try:
        keys = store.list_keys()
    finally:
        store.close()
    print("Granted identities:" if keys else "Granted identities: none (run `skdashboard-auth grant`)")
    for key in keys:
        print(f"  {key.fingerprint}  {'approved' if key.approved else 'pending'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skdashboard-auth", description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="install the login server and HTTPS dashboard")
    setup.add_argument("--hostname", help="HTTPS hostname (default: this node's Tailscale name)")
    setup.add_argument("--bind", help="IPv4 address to listen on (default: Tailscale IP)")
    setup.add_argument("--dashboard-port", type=int, default=7779)
    setup.add_argument("--idp-port", type=int, default=8421)
    setup.add_argument("--agent-home", type=Path, default=Path.home() / ".skcapstone")
    setup.add_argument("--tls-cert", type=Path, help="use this certificate instead of tailscale cert")
    setup.add_argument("--tls-key", type=Path)
    setup.add_argument("--no-start", action="store_true", help="write units without starting them")
    setup.set_defaults(func=cmd_setup)

    grant = sub.add_parser("grant", help="grant a CapAuth identity dashboard access")
    grant.add_argument("--pubkey", type=Path, help="armored public key (default: your CapAuth identity)")
    grant.set_defaults(func=cmd_grant)

    revoke = sub.add_parser("revoke", help="stop a CapAuth identity from logging in")
    revoke.add_argument("fingerprint")
    revoke.set_defaults(func=cmd_revoke)

    sub.add_parser("status", help="show URLs, services and grants").set_defaults(func=cmd_status)
    sub.add_parser("enable", help="require login on the local dashboard too").set_defaults(
        func=lambda a: _toggle(a, "on")
    )
    sub.add_parser("disable", help="no login for localhost on the local dashboard").set_defaults(
        func=lambda a: _toggle(a, "off")
    )
    sub.add_parser("renew-cert", help=argparse.SUPPRESS).set_defaults(func=cmd_renew_cert)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[-1:] or [""]
        sys.exit(f"command failed: {' '.join(map(str, exc.cmd[:3]))} ... {detail[0]}")


if __name__ == "__main__":
    main()
