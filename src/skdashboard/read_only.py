"""Dedicated read-only SKDashboard control-plane runtime."""

from __future__ import annotations

import argparse
import json
import logging
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, urlsplit

from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .control_plane_api import ALLOWED_BROWSER_ORIGINS
from .control_plane_api import routes as control_plane_routes
from .dashboard import _get_agent_status, _get_board_state

ALLOWED_BIND_HOSTS = frozenset({"127.0.0.1", "10.0.0.139", "100.81.238.58"})
HSTS_POLICY = "max-age=31536000"
READ_ONLY_STATIC_ASSETS = frozenset(
    {
        "css/board.css",
        "css/cockpit.css",
        "css/overview.css",
        "css/projects.css",
        "css/schedule.css",
        "js/control_plane_scope.js",
        "js/overview.js",
        "js/projects.js",
        "js/read_only_api.js",
        "js/schedule.js",
    }
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

    def page(name: str):
        async def serve(_request):
            html = (static_dir / name).read_text(encoding="utf-8")
            if legacy_board_url is not None:
                html = html.replace('href="/board"', f'href="{legacy_board_url}"')
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

        return serve

    async def index(_request):
        name = "read_only_session.html" if session_adapter is not None else "read_only.html"
        return await page(name)(_request)

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
        if relative in {"js/overview.js", "js/projects.js", "js/schedule.js"}:
            javascript = candidate.read_text(encoding="utf-8").replace(
                'from "./api.js"', 'from "./read_only_api.js"'
            )
            if relative == "js/overview.js":
                javascript = javascript.replace(
                    'import { openCard, initPanel } from "./editor.js";',
                    "const openCard = () => {};\nconst initPanel = () => {};",
                )
            if legacy_board_url is not None and relative in {
                "js/overview.js",
                "js/projects.js",
            }:
                javascript = javascript.replace('"/board"', json.dumps(legacy_board_url))
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

    routes = [
        Route("/", index),
        Route("/control-plane/now", page("overview.html")),
        Route("/control-plane/portfolio", page("projects.html")),
        Route("/control-plane/schedule", page("schedule.html")),
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
    parser.add_argument("--issuer-socket", type=Path)
    parser.add_argument("--legacy-board-url")
    parser.add_argument("--authorized-resource-id")
    parser.add_argument("--owner-policy-file", type=Path)
    parser.add_argument("--owner-policy-revision")
    parser.add_argument("--tenant-id")
    parser.add_argument("--issuer-uid", type=int)
    parser.add_argument("--owner-policy-uid", type=int)
    parser.add_argument("--operator-session-db", type=Path)
    parser.add_argument("--operator-policy-revisions-file", type=Path)
    parser.add_argument("--signer-fingerprint")
    parser.add_argument("--signer-gnupg-home", type=Path)
    parser.add_argument(
        "--capability-authorizer-factory",
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
        args.issuer_socket,
        args.legacy_board_url,
        args.authorized_resource_id,
        args.owner_policy_file,
        args.owner_policy_revision,
        args.tenant_id,
        args.capability_authorizer_factory,
        args.operator_session_db,
        args.operator_policy_revisions_file,
        args.signer_fingerprint,
        args.signer_gnupg_home,
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
        from importlib import import_module

        from capauth import (
            CurrentPolicyRevisions,
            OperatorSessionManager,
            SQLiteOperatorSessionBackend,
        )
        from skcoord.card_store import CardStore

        from .gpg_agent_signer import GPGAgentCredentialSigner
        from .live_control_plane import (
            LiveControlPlaneConfig,
            compose_file_backed_live_control_plane,
        )

        try:
            module_name, separator, attribute = args.capability_authorizer_factory.partition(":")
            if not separator or not module_name or not attribute:
                raise ValueError
            capability_authorizer = getattr(import_module(module_name), attribute)()
        except Exception as exc:
            parser.error(f"capability authorizer factory is unavailable: {type(exc).__name__}")
        try:

            def current_revisions(_binding=None):
                revisions_file = args.operator_policy_revisions_file
                status = revisions_file.stat()
                if status.st_mode & 0o022:
                    raise PermissionError
                return CurrentPolicyRevisions.model_validate_json(
                    revisions_file.read_text(encoding="utf-8"), strict=True
                )

            revisions = current_revisions()
            sessions = OperatorSessionManager(
                backend=SQLiteOperatorSessionBackend(args.operator_session_db),
                current_revisions=current_revisions,
                enabled=True,
            )
            signer = GPGAgentCredentialSigner(
                issuer_fingerprint=args.signer_fingerprint,
                gnupg_home=args.signer_gnupg_home,
            )
        except Exception as exc:
            parser.error(f"in-process authorization is unavailable: {type(exc).__name__}")
        config_options = {
            "issuer_socket": args.issuer_socket,
            "legacy_board_url": args.legacy_board_url,
            "resource_id": args.authorized_resource_id,
            "owner_policy_revision": args.owner_policy_revision,
            "tenant_id": args.tenant_id,
        }
        if args.issuer_uid is not None:
            config_options["issuer_uid"] = args.issuer_uid
        composition = compose_file_backed_live_control_plane(
            config=LiveControlPlaneConfig(**config_options),
            capability_authorizer=capability_authorizer,
            owner_policy_file=args.owner_policy_file,
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
            session_capability_issuer=composition.session_capability_issuer,
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
