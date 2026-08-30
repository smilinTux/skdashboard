"""Dedicated read-only SKDashboard control-plane runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import stat
from copy import deepcopy
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import quote, urlsplit

from starlette.applications import Starlette
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

from .control_plane_api import ALLOWED_BROWSER_ORIGINS
from .control_plane_api import routes as control_plane_routes
from .dashboard import _get_agent_status, _get_board_state
from .runtime_boundary import ALLOWED_BIND_HOSTS

HSTS_POLICY = "max-age=31536000"
RUNTIME_AUTHORIZER_FACTORY = "skdashboard.runtime_authorizer:build"
MAX_RUNTIME_POLICY_BYTES = 1 << 20
BUILD_INFO_SCHEMA = "skdashboard.build-info/v1"
BUILD_VALUE_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
)
READ_ONLY_STATIC_ASSETS = frozenset(
    {
        "css/ai.css",
        "css/architecture.css",
        "css/board.css",
        "css/cockpit.css",
        "css/governance.css",
        "css/overview.css",
        "css/projects.css",
        "css/reliability.css",
        "css/reports.css",
        "css/schedule.css",
        "js/ai.js",
        "js/architecture.js",
        "js/control_plane_scope.js",
        "js/governance.js",
        "js/overview.js",
        "js/projects.js",
        "js/read_only_api.js",
        "js/reliability.js",
        "js/reports.js",
        "js/schedule.js",
    }
)
LEGACY_RUNTIME_PATHS = (
    "/cockpit",
    "/cmdb",
    "/board",
    "/assistant",
    "/trust",
    "/models",
    "/economy",
    "/fleet",
)


class CallbackAccessLogFilter(logging.Filter):
    """Remove OIDC callback query values from Uvicorn access records."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = args[2]
            if isinstance(path, str) and path.partition("?")[0] == "/auth/callback":
                record.args = (*args[:2], "/auth/callback", *args[3:])
        return True


class SecureTransportMiddleware:
    """Deny unnamed hosts and enforce HTTPS response transport."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        authority = headers.get(b"host", b"").decode("ascii", "ignore")
        host = authority.rsplit(":", 1)[0].lower()
        if host not in ALLOWED_BIND_HOSTS:
            await _plain_response(send, 400, b"named host required")
            return
        if scope["scheme"] != "https":
            path = quote(scope.get("path", "/"), safe="/%:@")
            query = scope.get("query_string", b"")
            location = f"https://{authority}{path}".encode()
            if query:
                location += b"?" + query
            await _plain_response(send, 308, b"HTTPS required", [(b"location", location)])
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                secured = [(b"strict-transport-security", HSTS_POLICY.encode())]
                for key, value in message.get("headers", []):
                    if key.lower() == b"set-cookie":
                        value = _secure_host_only_cookie(value)
                    secured.append((key, value))
                message["headers"] = secured
            await send(message)

        await self.app(scope, receive, secure_send)


async def _plain_response(send, status: int, body: bytes, headers=()):
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), *headers],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _secure_host_only_cookie(value: bytes) -> bytes:
    parts = [part.strip() for part in value.decode("latin-1").split(";")]
    attributes = [part for part in parts[1:] if not part.lower().startswith("domain=")]
    if not any(part.lower() == "secure" for part in attributes):
        attributes.append("Secure")
    return "; ".join([parts[0], *attributes]).encode("latin-1")


def _bounded_build_value(value: str | None) -> str:
    candidate = (value or "").strip()
    if (
        not candidate
        or len(candidate) > 128
        or any(c not in BUILD_VALUE_CHARACTERS for c in candidate)
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
        if len(source_commit) < 7 or any(c not in "0123456789abcdefABCDEF" for c in source_commit):
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


def _safe_identity(value) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_exact_value_free_config(path: Path, expected_sha256: str, *, expected_uid: int) -> bytes:
    if (
        len(expected_sha256) != 64
        or expected_sha256.lower() != expected_sha256
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("an exact lowercase configuration SHA256 is required")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None or path.name in {"", ".", ".."}:
        raise ValueError("configuration path is unsafe")
    directory = os.open(path.parent, os.O_RDONLY | directory_flag | no_follow)
    descriptor = -1
    try:
        parent = os.fstat(directory)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != expected_uid
            or parent.st_mode & 0o022
        ):
            raise ValueError("configuration directory is unsafe")
        listed = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        descriptor = os.open(path.name, os.O_RDONLY | no_follow, dir_fd=directory)
        opened = os.fstat(descriptor)
        identity = _safe_identity(opened)
        if (
            _safe_identity(listed) != identity
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != expected_uid
            or opened.st_nlink != 1
            or opened.st_mode & 0o022
            or opened.st_size > MAX_RUNTIME_POLICY_BYTES
        ):
            raise ValueError("configuration file is unsafe")
        payload = bytearray()
        while len(payload) <= MAX_RUNTIME_POLICY_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_RUNTIME_POLICY_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_RUNTIME_POLICY_BYTES:
            raise ValueError("configuration file exceeds the safe cap")
        if (
            _safe_identity(os.stat(path.name, dir_fd=directory, follow_symlinks=False)) != identity
            or _safe_identity(os.fstat(descriptor)) != identity
        ):
            raise ValueError("configuration changed during read")
        result = bytes(payload)
        if hashlib.sha256(result).hexdigest() != expected_sha256:
            raise ValueError("configuration SHA256 mismatch")
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def create_read_only_app(
    home: Path,
    *,
    authorizer=None,
    decision_authorizer=None,
    invocation_factory=None,
    project_provider=None,
    schedule_provider=None,
    schedule_forecast_provider=None,
    reliability_provider=None,
    session_adapter=None,
    session_capability_issuer=None,
    session_authorizer=None,
    architecture_provider=None,
    governance_provider=None,
    report_provider=None,
    legacy_board_url=None,
) -> Starlette:
    """Build the least-privilege app without importing legacy route tables."""

    static_dir = Path(__file__).parent / "static"
    if reliability_provider is None and decision_authorizer is not None:
        from .dashboard_itil import ReliabilityProjectionProvider

        reliability_provider = ReliabilityProjectionProvider()
    if architecture_provider is None and decision_authorizer is not None:
        from .dashboard_architecture import ArchitectureProjectionProvider

        architecture_provider = ArchitectureProjectionProvider()
    if governance_provider is None and decision_authorizer is not None:
        from .dashboard_governance import GovernanceProjectionProvider

        governance_provider = GovernanceProjectionProvider()
    if report_provider is None and decision_authorizer is not None:
        from .dashboard_reports import ReportProjectionProvider

        report_provider = ReportProjectionProvider()

    if legacy_board_url is not None:
        parsed_board_url = urlsplit(legacy_board_url)
        if (
            parsed_board_url.scheme != "https"
            or not parsed_board_url.netloc
            or parsed_board_url.username is not None
            or parsed_board_url.password is not None
            or parsed_board_url.query
            or parsed_board_url.fragment
        ):
            raise ValueError("legacy board URL must be a credential-free exact HTTPS URL")
        legacy_runtime_urls = {
            path: (
                legacy_board_url
                if path == "/board"
                else f"{parsed_board_url.scheme}://{parsed_board_url.netloc}{path}"
            )
            for path in LEGACY_RUNTIME_PATHS
        }
    else:
        legacy_runtime_urls = {}

    def page(name: str):
        async def serve(_request):
            html = (static_dir / name).read_text(encoding="utf-8")
            for path, url in legacy_runtime_urls.items():
                html = html.replace(f'href="{path}"', f'href="{url}"')
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

        return serve

    async def index(_request):
        name = "read_only_session.html" if session_adapter is not None else "read_only.html"
        return await page(name)(_request)

    def redirect(location: str):
        async def serve(_request):
            return RedirectResponse(
                location, status_code=307, headers={"Cache-Control": "no-store"}
            )

        return serve

    async def board_workspace(_request):
        board_url = legacy_runtime_urls.get("/board")
        if board_url is None:
            return HTMLResponse(
                "<h1>Work queue unavailable</h1>"
                "<p>No authorized legacy board source is configured.</p>",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        return RedirectResponse(board_url, status_code=307, headers={"Cache-Control": "no-store"})

    async def static_asset(request):
        relative = request.url.path.removeprefix("/static/")
        if relative not in READ_ONLY_STATIC_ASSETS:
            return JSONResponse({"error": "not_found"}, status_code=404)
        candidate = (static_dir / relative).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not candidate.is_file():
            return JSONResponse({"error": "not_found"}, status_code=404)
        if relative in {
            "js/ai.js",
            "js/architecture.js",
            "js/governance.js",
            "js/overview.js",
            "js/projects.js",
            "js/reliability.js",
            "js/reports.js",
            "js/schedule.js",
        }:
            javascript = candidate.read_text(encoding="utf-8").replace(
                'from "./api.js"', 'from "./read_only_api.js"'
            )
            if relative == "js/overview.js":
                javascript = javascript.replace(
                    'import { openCard, initPanel } from "./editor.js";',
                    "const openCard = () => {};\nconst initPanel = () => {};",
                )
            for path, url in legacy_runtime_urls.items():
                javascript = javascript.replace(json.dumps(path), json.dumps(url))
            return Response(javascript, media_type="text/javascript")
        return FileResponse(candidate)

    async def manifest(request):
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "schemaVersion": "1.1",
                "id": "skdashboard-read-only",
                "name": "SK Control Plane",
                "grade": "B",
                "entry": {"url": f"{base}/"},
                "nav": {"icon": "dashboard", "order": 40, "label": "Control Plane"},
                "auth": {"audience": "skdashboard", "scopes": ["skdashboard.read"]},
                "health": f"{base}/api/v1/health",
            }
        )

    async def build_information(_request):
        return JSONResponse(_build_information(), headers={"Cache-Control": "no-store"})

    routes = [
        Route("/", index),
        Route("/control-plane/now", page("overview.html")),
        Route("/control-plane/portfolio", page("projects.html")),
        Route("/control-plane/schedule", page("schedule.html")),
        Route(
            "/matters",
            redirect(
                "/control-plane/now?role=governance&scope=estate&window=latest"
                "&baseline=none&service=all&selected_silo=legal"
            ),
        ),
        Route("/tasks", board_workspace),
        Route("/work-queue", board_workspace),
        Route("/control-plane/reliability", page("reliability.html")),
        Route("/control-plane/architecture", page("architecture.html")),
        Route("/control-plane/ai", page("ai.html")),
        Route("/control-plane/governance", page("governance.html")),
        Route("/control-plane/reports", page("reports.html")),
        Route("/api/v1/build-info", build_information),
        Route("/.well-known/skworld-module.json", manifest),
    ]
    routes.extend(
        control_plane_routes(
            home,
            board_reader=_get_board_state,
            health_reader=_get_agent_status,
            authorizer=authorizer,
            decision_authorizer=decision_authorizer,
            invocation_factory=invocation_factory,
            project_provider=project_provider,
            schedule_provider=schedule_provider,
            schedule_forecast_provider=schedule_forecast_provider,
            reliability_provider=reliability_provider,
            session_resolver=session_adapter.resolve if session_adapter else None,
            session_capability_issuer=session_capability_issuer,
            session_authorizer=session_authorizer,
            architecture_provider=architecture_provider,
            governance_provider=governance_provider,
            report_provider=report_provider,
        )
    )
    if session_adapter is not None:
        routes.extend(session_adapter.routes())
    for asset in sorted(READ_ONLY_STATIC_ASSETS):
        routes.append(
            Route(
                f"/static/{asset}",
                static_asset,
                name=asset,
            )
        )
    app = Starlette(routes=routes)
    app.add_middleware(SecureTransportMiddleware)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the read-only SKDashboard control plane")
    parser.add_argument("--home", type=Path, default=Path.home() / ".skcapstone")
    parser.add_argument("--host", required=True, choices=sorted(ALLOWED_BIND_HOSTS))
    parser.add_argument("--port", type=int, default=7778)
    parser.add_argument("--tls-certfile", type=Path, required=True)
    parser.add_argument("--tls-keyfile", type=Path, required=True)
    parser.add_argument("--session-db", type=Path)
    parser.add_argument("--session-key-file", type=Path)
    parser.add_argument("--oidc-issuer")
    parser.add_argument("--oidc-redirect-uri")
    parser.add_argument("--oidc-client-secret-file", type=Path)
    parser.add_argument("--legacy-board-url")
    parser.add_argument("--authorized-resource-id")
    parser.add_argument("--owner-policy-file", type=Path)
    parser.add_argument("--owner-policy-sha256")
    parser.add_argument("--owner-policy-revision")
    parser.add_argument("--tenant-id")
    parser.add_argument("--node-id", choices=("chiap04", "chiap08"), default="chiap08")
    parser.add_argument("--owner-policy-uid", type=int)
    parser.add_argument("--operator-session-db", type=Path)
    parser.add_argument("--operator-policy-revisions-file", type=Path)
    parser.add_argument("--operator-policy-revisions-sha256")
    parser.add_argument("--signer-fingerprint")
    parser.add_argument("--signer-gnupg-home", type=Path)
    parser.add_argument("--signer-passphrase-file", type=Path)
    parser.add_argument("--trusted-issuer-policy-file", type=Path)
    parser.add_argument("--trusted-issuer-policy-sha256")
    parser.add_argument("--trusted-issuer-signature-file", type=Path)
    parser.add_argument("--trusted-issuer-signature-sha256")
    parser.add_argument("--capability-state-db", type=Path)
    parser.add_argument(
        "--capability-authorizer-factory",
        choices=(RUNTIME_AUTHORIZER_FACTORY,),
        help="module:callable returning the approved durable CapAuth authorizer",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    session_values = (
        args.session_db,
        args.session_key_file,
        args.oidc_issuer,
        args.oidc_redirect_uri,
        args.oidc_client_secret_file,
    )
    if any(session_values) and not all(session_values):
        parser.error("all session and OIDC options are required together")
    live_values = (
        args.legacy_board_url,
        args.authorized_resource_id,
        args.owner_policy_file,
        args.owner_policy_sha256,
        args.owner_policy_revision,
        args.tenant_id,
        args.capability_authorizer_factory,
        args.operator_session_db,
        args.operator_policy_revisions_file,
        args.operator_policy_revisions_sha256,
        args.signer_fingerprint,
        args.signer_gnupg_home,
        args.signer_passphrase_file,
        args.trusted_issuer_policy_file,
        args.trusted_issuer_policy_sha256,
        args.trusted_issuer_signature_file,
        args.trusted_issuer_signature_sha256,
        args.capability_state_db,
    )
    if any(live_values) and not all(live_values):
        parser.error("all live control-plane options are required together")
    if all(live_values) and not all(session_values):
        parser.error("live control-plane composition requires the same-origin session options")

    session_adapter = None
    if all(session_values):
        from .session_adapter import EncryptedSessionAdapter, SessionConfig

        if args.oidc_client_secret_file.stat().st_mode & 0o077:
            parser.error("OIDC client secret file must be mode 0600")
        session_adapter = EncryptedSessionAdapter(
            args.session_db,
            args.session_key_file,
            SessionConfig(
                issuer=args.oidc_issuer,
                redirect_uri=args.oidc_redirect_uri,
                client_secret=args.oidc_client_secret_file.read_text(encoding="utf-8").strip(),
            ),
        )

    app_options = {"session_adapter": session_adapter}
    if all(live_values):
        from capauth import (
            CurrentPolicyRevisions,
            OperatorSessionManager,
            Principal,
            SQLiteOperatorSessionBackend,
        )
        from skcoord.authorized_card_policy import AuthorizedCardPolicyDocumentV1
        from skcoord.card_store import CardStore

        from .gpg_agent_signer import GPGAgentCredentialSigner
        from .live_control_plane import (
            LiveControlPlaneConfig,
            compose_file_backed_live_control_plane,
        )
        from .runtime_authorizer import build as build_capability_authorizer

        try:
            expected_uid = args.owner_policy_uid
            if expected_uid is None:
                expected_uid = os.geteuid()
            owner_payload = _read_exact_value_free_config(
                args.owner_policy_file,
                args.owner_policy_sha256,
                expected_uid=expected_uid,
            )
            owner_document = AuthorizedCardPolicyDocumentV1.model_validate_json(owner_payload)
            current = datetime.now(timezone.utc)
            matching_entries = tuple(
                entry.node_id == args.node_id
                and entry.resource_id == args.authorized_resource_id
                and entry.owner_policy_revision == args.owner_policy_revision
                and entry.valid_from <= current < entry.expires_at
                for entry in owner_document.entries
            )
            selected_entries = tuple(
                entry
                for entry, matches in zip(owner_document.entries, matching_entries)
                if matches
            )
            if len(selected_entries) != 1:
                raise ValueError
            selected_entry = selected_entries[0]
            if (
                selected_entry.subject != "C8D406A46F2DF4894E4FB41580A638570C9D41C4"
                or selected_entry.acting_principal_id != selected_entry.subject
            ):
                raise ValueError
            revisions_payload = _read_exact_value_free_config(
                args.operator_policy_revisions_file,
                args.operator_policy_revisions_sha256,
                expected_uid=expected_uid,
            )
            revisions = CurrentPolicyRevisions.model_validate_json(revisions_payload, strict=True)
            if (
                revisions.owner != args.owner_policy_revision
                or revisions.issuer != args.trusted_issuer_policy_sha256
            ):
                raise ValueError
            capability_authorizer = build_capability_authorizer(
                trusted_issuer_policy_file=args.trusted_issuer_policy_file,
                trusted_issuer_policy_sha256=args.trusted_issuer_policy_sha256,
                trusted_issuer_signature_file=args.trusted_issuer_signature_file,
                trusted_issuer_signature_sha256=args.trusted_issuer_signature_sha256,
                issuer_fingerprint=args.signer_fingerprint,
                verifier_gnupg_home=args.signer_gnupg_home,
                principal=Principal(
                    principal_id=selected_entry.acting_principal_id,
                    subject=selected_entry.subject,
                    kind="human",
                ),
                principal_revision=revisions.principal,
                state_db=args.capability_state_db,
                expected_uid=expected_uid,
            )
        except Exception as exc:
            parser.error(f"runtime policy composition is unavailable: {type(exc).__name__}")
        try:

            def current_revisions(_binding=None):
                payload = _read_exact_value_free_config(
                    args.operator_policy_revisions_file,
                    args.operator_policy_revisions_sha256,
                    expected_uid=expected_uid,
                )
                return CurrentPolicyRevisions.model_validate_json(payload, strict=True)

            sessions = OperatorSessionManager(
                backend=SQLiteOperatorSessionBackend(args.operator_session_db),
                current_revisions=current_revisions,
                enabled=True,
            )
            signer = GPGAgentCredentialSigner(
                issuer_fingerprint=args.signer_fingerprint,
                gnupg_home=args.signer_gnupg_home,
                passphrase_file=args.signer_passphrase_file,
            )
        except Exception as exc:
            parser.error(f"in-process authorization is unavailable: {type(exc).__name__}")
        config_options = {
            "legacy_board_url": args.legacy_board_url,
            "resource_id": args.authorized_resource_id,
            "owner_policy_revision": args.owner_policy_revision,
            "tenant_id": args.tenant_id,
            "node_id": args.node_id,
        }
        composition = compose_file_backed_live_control_plane(
            config=LiveControlPlaneConfig(**config_options),
            capability_authorizer=capability_authorizer,
            owner_policy_file=args.owner_policy_file,
            owner_policy_document=owner_document,
            expected_policy_uid=args.owner_policy_uid,
            store_factory=CardStore,
            credential_signer=signer,
            operator_sessions=sessions,
            operator_revisions=revisions,
        )
        session_adapter.control_plane_bridge = composition.session_authorizer
        app_options.update(
            decision_authorizer=composition.decision_authorizer,
            invocation_factory=composition.invocation_factory,
            project_provider=composition.project_provider,
            schedule_provider=composition.schedule_provider,
            session_authorizer=composition.session_authorizer,
            legacy_board_url=composition.legacy_board_url,
            schedule_forecast_provider=None,
        )

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    log_config = deepcopy(LOGGING_CONFIG)
    log_config["filters"] = {
        **log_config.get("filters", {}),
        "redact_oidc_callback": {"()": CallbackAccessLogFilter},
    }
    access_handler = log_config["handlers"]["access"]
    access_handler["filters"] = [
        *access_handler.get("filters", []),
        "redact_oidc_callback",
    ]

    uvicorn.run(
        create_read_only_app(args.home, **app_options),
        host=args.host,
        port=args.port,
        ssl_certfile=str(args.tls_certfile),
        ssl_keyfile=str(args.tls_keyfile),
        log_config=log_config,
    )


__all__ = [
    "ALLOWED_BIND_HOSTS",
    "ALLOWED_BROWSER_ORIGINS",
    "CallbackAccessLogFilter",
    "HSTS_POLICY",
    "SecureTransportMiddleware",
    "create_read_only_app",
    "main",
]
