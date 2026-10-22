"""Frozen v1 read-only control-plane projections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import uuid4

from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

SCHEMA_VERSION = "1.1.0"
MAX_LIMIT = 200
MAX_BEARER_BYTES = 64 * 1024
PROJECTION_CACHE_ENTRIES = 64
PROJECTION_CACHE_TTL_SECONDS = 60
RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60
ALLOWED_BROWSER_ORIGINS = frozenset(
    {"https://10.0.0.139:7778", "https://100.81.238.58:7778"}
)


class ControlPlaneInvocationFactory(Protocol):
    """Build trusted invocation facts without consulting bearer claims."""

    def __call__(self, request, capability: str, target: str): ...


class ProjectionCache:
    """Small process-local cache; owners explicitly invalidate changed projections."""

    def __init__(
        self,
        *,
        max_entries: int = PROJECTION_CACHE_ENTRIES,
        ttl_seconds: int = PROJECTION_CACHE_TTL_SECONDS,
    ) -> None:
        if max_entries < 1 or ttl_seconds < 1:
            raise ValueError("projection cache bounds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0
        self.invalidations = 0

    def get(self, key: tuple):
        now = time.monotonic()
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                self.misses += 1
                return None
            inserted, value = entry
            if inserted <= now - self.ttl_seconds:
                self.misses += 1
                return None
            self._entries[key] = entry
            self.hits += 1
            return value

    def put(self, key: tuple, value: object) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = (time.monotonic(), value)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, projection: str | None = None) -> None:
        """Explicitly discard one owner projection family, or every family."""
        with self._lock:
            if projection is None:
                removed = len(self._entries)
                self._entries.clear()
            else:
                keys = [key for key in self._entries if key and key[0] == projection]
                removed = len(keys)
                for key in keys:
                    del self._entries[key]
            self.invalidations += removed

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error(request, status: int, code: str, message: str, *, retryable: bool = False):
    request_id = request.headers.get("x-request-id", "")[:128] or uuid4().hex
    return JSONResponse(
        {"code": code, "message": message, "retryable": retryable, "request_id": request_id},
        status_code=status,
    )


def _limit(request) -> int:
    raw = request.query_params.get("limit", "50")
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _cursor(raw: str | None) -> int:
    if not raw:
        return 0
    if len(raw) > 512:
        raise ValueError("cursor is too long")
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
        prefix, value = decoded.split(":", 1)
        if prefix != "v1":
            raise ValueError
        offset = int(value)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if offset < 0:
        raise ValueError("cursor is invalid")
    return offset


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"v1:{offset}".encode()).decode().rstrip("=")


def _visibility() -> dict:
    # SKCP-02 adds capability authorization. Until then, this local read plane
    # is explicitly visible and never manufactures a policy decision.
    return {"state": "visible", "authorization": "authorized"}


def _envelope(
    request,
    owner: str,
    items: list[dict],
    errors: list[str],
    *,
    observed_at=None,
    truth_state=None,
    scope=None,
):
    projected_at = _now()
    truth = truth_state or ("partial" if errors else ("current" if items else "unknown"))
    request_id = request.headers.get("x-request-id", "")[:128] or uuid4().hex
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "source_owner": owner,
        "scope": dict(scope or {}),
        "freshness": {
            "truth_state": truth,
            "visibility": _visibility(),
            "observed_at": observed_at or projected_at,
            "projected_at": projected_at,
            "ttl_seconds": 60,
            "age_seconds": 0,
        },
        "visibility": _visibility(),
        "metrics": [],
        "items": items,
        "errors": [
            {
                "code": "SOURCE_PARTIAL",
                "message": str(message)[:500],
                "retryable": True,
                "request_id": request_id,
            }
            for message in errors[:64]
        ],
    }
    if not items and not errors:
        envelope["errors"] = [{
            "code": "SOURCE_UNKNOWN",
            "message": "The source returned no observations",
            "retryable": True,
            "request_id": request_id,
        }]
    return envelope


def _page(request, owner: str, items: list[dict], errors: list[str], *, observed_at=None):
    limit = _limit(request)
    offset = _cursor(request.query_params.get("cursor"))
    if offset > len(items):
        raise ValueError("cursor is outside the result set")
    page_items = items[offset : offset + limit]
    result = _envelope(request, owner, page_items, errors, observed_at=observed_at)
    next_offset = offset + len(page_items)
    result["page"] = {
        "limit": limit,
        "next_cursor": _encode_cursor(next_offset) if next_offset < len(items) else None,
        "has_more": next_offset < len(items),
    }
    return result


def _response(request, body: dict):
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    # Request IDs and projection clocks are delivery metadata, not source
    # changes. Hash only the bounded projection so conditional GETs are useful.
    projection = {
        key: body.get(key)
        for key in ("schema_version", "source_owner", "scope", "metrics", "items", "page", "errors")
    }
    projection["errors"] = [
        {key: value for key, value in error.items() if key != "request_id"}
        for error in projection.get("errors") or []
    ]
    def source_projection(value):
        if isinstance(value, dict):
            return {
                key: source_projection(
                    {
                        child_key: child_value
                        for child_key, child_value in item.items()
                        if child_key not in {"capauth_decision_id", "expires_at"}
                    }
                    if key == "policy_decision" and isinstance(item, dict)
                    else item
                )
                for key, item in value.items()
                if key not in {"projected_at", "observed_at", "age_seconds", "request_id"}
            }
        if isinstance(value, list):
            return [source_projection(item) for item in value]
        return value

    etag_bytes = json.dumps(
        source_projection(projection), sort_keys=True, separators=(",", ":")
    ).encode()
    etag = f'"{hashlib.sha256(etag_bytes).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(serialized, media_type="application/json", headers={"ETag": etag})


def _capauth_authorize(home: Path, bearer: str, capability: str, target: str) -> bool:
    """Verify one bounded audience token and its current CapAuth policy."""

    try:
        from capauth.authz import decide
        from capauth.tokens import has_scope, import_token, verify_audience_token

        decoded = base64.b64decode(bearer.encode("ascii"), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(decoded).decode("ascii") != bearer:
            return False
        token = import_token(decoded.decode("utf-8"))
        payload = token.payload
        if (
            payload.expires_at is None
            or payload.expires_at <= payload.issued_at
            or (payload.expires_at - payload.issued_at).total_seconds() > 300
            or "*" in payload.capabilities
            or not has_scope(token, capability)
            or not verify_audience_token(token, "skdashboard", home=home)
        ):
            return False
        decision = decide(
            payload.subject,
            capability,
            resource={"target": target},
            context={"service": "skdashboard-api"},
            base_dir=home,
        )
        return bool(decision.allow)
    except Exception:
        return False


def _clear_decision(request) -> None:
    verifier = getattr(request.state, "control_plane_currentness_verifier", None)
    try:
        from capauth import ControlPlaneCurrentnessVerifier

        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
    except Exception:
        pass
    try:
        del request.state.control_plane_currentness_verifier
    except (AttributeError, KeyError):
        pass
    try:
        del request.state.control_plane_decision
    except (AttributeError, KeyError):
        pass
    try:
        del request.state.control_plane_cache_scope
    except (AttributeError, KeyError):
        pass


def _typed_context(
    request,
    bearer: str,
    capability: str,
    target: str,
    *,
    decision_authorizer,
    invocation_factory: ControlPlaneInvocationFactory,
):
    """Return one exact sanitized CapAuth context or fail closed."""

    from capauth import (
        ClientKind,
        ControlPlaneAuthorizationResultV1,
        ControlPlaneCurrentnessVerifier,
        ControlPlaneInvocationV1,
        DecisionCode,
        DecisionState,
        SanitizedControlPlaneDecisionV1,
    )

    invocation = invocation_factory(request, capability, target)
    if type(invocation) is not ControlPlaneInvocationV1:
        return None
    boundary = invocation.boundary
    observed_origin = request.headers.get("origin")
    if boundary.client_kind is ClientKind.BROWSER:
        if observed_origin is None or boundary.origin != observed_origin:
            return None
    elif observed_origin is not None:
        return None
    verifier = None
    authorize_with_currentness = getattr(
        decision_authorizer, "authorize_with_currentness", None
    )
    if callable(authorize_with_currentness):
        issued = authorize_with_currentness(bearer, invocation)
        if type(issued) is not tuple or len(issued) != 2:
            if type(issued) in {tuple, list}:
                for value in issued:
                    if type(value) is ControlPlaneCurrentnessVerifier:
                        value.close()
            return None
        result, verifier = issued
        if type(verifier) is not ControlPlaneCurrentnessVerifier:
            return None
    else:
        result = decision_authorizer.authorize(bearer, invocation)
    if type(result) is not ControlPlaneAuthorizationResultV1:
        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
        return None
    if (
        not result.allow
        or result.state is not DecisionState.ALLOW
        or result.code is not DecisionCode.ALLOW
        or type(result.context) is not SanitizedControlPlaneDecisionV1
    ):
        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
        return None
    try:
        # Revalidate even model_construct output before it reaches request state.
        validated = SanitizedControlPlaneDecisionV1(
            **{
                name: getattr(result.context, name)
                for name in SanitizedControlPlaneDecisionV1.model_fields
            }
        )
    except Exception:
        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
        return None
    if validated != result.context:
        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
        return None
    context = result.context
    binding = context.binding
    if (
        context.boundary != boundary
        or binding.node_id != invocation.node_id
        or binding.purpose != invocation.purpose
        or binding.audience != invocation.audience
        or binding.capability != capability
        or binding.target != target
        or binding.resource_type != invocation.resource_type
        or binding.resource_id != invocation.resource_id
        or context.capauth_decision.correlation_id != invocation.correlation_id
        or context.joined_decision.scope != binding.capability_scope()
    ):
        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
        return None
    now = datetime.now(timezone.utc)
    if not context.issued_at <= now < context.expires_at:
        if type(verifier) is ControlPlaneCurrentnessVerifier:
            verifier.close()
        return None
    return context, verifier


def _protected_handler(
    handler,
    capability: str,
    *,
    authorize,
    decision_authorizer,
    invocation_factory,
    counters,
    session_resolver=None,
):
    async def wrapped(request):
        _clear_decision(request)
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_BROWSER_ORIGINS:
            counters["denied"] += 1
            response = _error(request, 403, "ORIGIN_DENIED", "browser origin is not allowed")
            response.headers["Cache-Control"] = "no-store"
            return response
        header = request.headers.get("authorization", "")
        if session_resolver is not None and not header:
            try:
                resolved = await session_resolver(request)
                state = getattr(resolved, "state", None)
                if state in {"corrupt", "unavailable"}:
                    counters["denied"] += 1
                    response = _error(request, 503, "SESSION_UNAVAILABLE", "session authorization is temporarily unavailable", retryable=True)
                    response.headers["Retry-After"] = "5"
                    response.headers["Cache-Control"] = "no-store"
                    return response
                bearer = getattr(resolved, "access_token", resolved)
            except Exception:
                bearer = None
            if bearer:
                header = f"Bearer {bearer}"
        if not header.startswith("Bearer ") or header.count(" ") != 1:
            counters["denied"] += 1
            response = _error(request, 401, "UNAUTHORIZED", "a bearer capability is required")
            response.headers["Cache-Control"] = "no-store"
            return response
        bearer = header[7:]
        if not bearer or len(bearer.encode()) > MAX_BEARER_BYTES:
            counters["denied"] += 1
            response = _error(request, 401, "UNAUTHORIZED", "the bearer capability is invalid")
            response.headers["Cache-Control"] = "no-store"
            return response
        if decision_authorizer is not None:
            try:
                authority = _typed_context(
                    request,
                    bearer,
                    capability,
                    request.url.path,
                    decision_authorizer=decision_authorizer,
                    invocation_factory=invocation_factory,
                )
            except Exception:
                authority = None
            if authority is None:
                counters["denied"] += 1
                response = _error(
                    request, 403, "FORBIDDEN", "the capability decision denied access"
                )
                response.headers["Cache-Control"] = "no-store"
                return response
            context, verifier = authority
            request.state.control_plane_decision = context
            if verifier is not None:
                request.state.control_plane_currentness_verifier = verifier
        else:
            try:
                allowed = authorize(bearer, capability, request.url.path)
            except Exception:
                allowed = False
            if not allowed:
                counters["denied"] += 1
                response = _error(
                    request, 403, "FORBIDDEN", "the capability decision denied access"
                )
                response.headers["Cache-Control"] = "no-store"
                return response
        scope_material = bearer
        context = getattr(request.state, "control_plane_decision", None)
        if context is not None:
            try:
                scope_material += json.dumps(
                    context.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                )
            except Exception:
                scope_material += repr(context)
        request.state.control_plane_cache_scope = hashlib.sha256(
            f"{capability}\0{request.url.path}\0{scope_material}".encode()
        ).hexdigest()
        try:
            response = await handler(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            _clear_decision(request)

    return wrapped


def routes(
    home: Path,
    *,
    board_reader,
    health_reader,
    authorizer=None,
    decision_authorizer=None,
    invocation_factory: ControlPlaneInvocationFactory | None = None,
    project_provider=None,
    schedule_provider=None,
    reliability_provider=None,
    session_resolver=None,
    architecture_provider=None,
    governance_provider=None,
    report_provider=None,
    projection_cache: ProjectionCache | None = None,
):
    if (decision_authorizer is None) != (invocation_factory is None):
        raise ValueError("typed control-plane authorization requires both injected components")
    if (
        project_provider is not None
        or schedule_provider is not None
        or reliability_provider is not None
        or architecture_provider is not None
        or governance_provider is not None
        or report_provider is not None
    ) and decision_authorizer is None:
        raise ValueError("owner projection requires typed control-plane authorization")
    hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
    counters = {
        "requests": 0,
        "denied": 0,
        "rate_accepted": defaultdict(int),
        "rate_rejected": defaultdict(int),
    }
    projection_cache = projection_cache or ProjectionCache()
    cursor_secret = secrets.token_bytes(32)
    authorize = authorizer or (
        lambda bearer, capability, target: _capauth_authorize(
            home, bearer, capability, target
        )
    )

    def limited(handler, rate_class: str = "common"):
        async def wrapped(request):
            now = time.monotonic()
            client = request.client.host if request.client else "local"
            recent = hits[(rate_class, client)]
            while recent and recent[0] <= now - RATE_WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= RATE_LIMIT:
                counters["rate_rejected"][rate_class] += 1
                response = _error(request, 429, "RATE_LIMITED", "read rate limit exceeded", retryable=True)
                response.headers["Retry-After"] = str(RATE_WINDOW_SECONDS)
                response.headers["X-RateLimit-Class"] = rate_class
                return response
            recent.append(now)
            counters["requests"] += 1
            counters["rate_accepted"][rate_class] += 1
            response = await handler(request)
            response.headers["X-RateLimit-Class"] = rate_class
            return response

        return wrapped

    def protected(handler, capability: str, *, rate_class: str = "common"):
        return limited(
            _protected_handler(
                handler,
                capability,
                authorize=authorize,
                decision_authorizer=decision_authorizer,
                invocation_factory=invocation_factory,
                counters=counters,
                session_resolver=session_resolver,
            ),
            rate_class,
        )

    async def health(request):
        raw = health_reader(home)
        errors = [raw["error"]] if raw.get("error") else []
        safe = [] if errors else [{
            "component": "skcapstone",
            "state": raw.get("consciousness", "unknown").lower(),
            "pillars": raw.get("pillars", {}),
        }]
        return _response(request, _envelope(request, "skcapstone", safe, errors))

    def page_scope(request) -> str:
        pairs = sorted(
            (key, value)
            for key, value in request.query_params.multi_items()
            if key != "cursor"
        )
        material = json.dumps(
            [pairs, getattr(request.state, "control_plane_cache_scope", "public")],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(material).hexdigest()

    def decode_page_cursor(raw: str | None, scope: str) -> tuple[int, str | None]:
        if not raw:
            return 0, None
        if len(raw) > 512:
            raise ValueError("cursor is too long")
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            payload, signature = decoded.rsplit(b".", 1)
            if not hmac.compare_digest(
                signature, hmac.new(cursor_secret, payload, hashlib.sha256).hexdigest().encode()
            ):
                raise ValueError
            value = json.loads(payload)
            if (
                set(value) != {"offset", "revision", "scope"}
                or value["scope"] != scope
                or not isinstance(value["offset"], int)
                or value["offset"] < 0
                or not isinstance(value["revision"], str)
                or not value["revision"]
                or len(value["revision"]) > 256
            ):
                raise ValueError
            return value["offset"], value["revision"]
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("cursor is invalid or its scope changed") from exc

    def encode_page_cursor(offset: int, revision: str, scope: str) -> str:
        payload = json.dumps(
            {"offset": offset, "revision": revision, "scope": scope},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(cursor_secret, payload, hashlib.sha256).hexdigest().encode()
        return base64.urlsafe_b64encode(payload + b"." + signature).decode().rstrip("=")

    async def board(request):
        try:
            limit = _limit(request)
            scope = page_scope(request)
            offset, expected_revision = decode_page_cursor(
                request.query_params.get("cursor"), scope
            )
            cache_key = ("board", scope, offset, limit, expected_revision)
            cached = projection_cache.get(cache_key)
            cache_state = "hit"
            if cached is None:
                cache_state = "miss"
                raw = board_reader(
                    home,
                    offset=offset,
                    limit=limit + 1,
                    expected_revision=expected_revision,
                )
                if not isinstance(raw, dict):
                    raise RuntimeError("owner returned an invalid page")
                tasks = raw.get("tasks", [])
                revision = raw.get("revision")
                if (
                    not isinstance(tasks, list)
                    or len(tasks) > limit + 1
                    or (revision is not None and (not isinstance(revision, str) or len(revision) > 256))
                ):
                    raise RuntimeError("owner exceeded the bounded page contract")
                if expected_revision is not None and revision != expected_revision:
                    raise ValueError("cursor source revision changed")
                if raw.get("error"):
                    return _error(
                        request,
                        503,
                        "SOURCE_UNAVAILABLE",
                        "the bounded board owner is unavailable",
                        retryable=True,
                    )
                cached = (tasks, None, revision, raw.get("observed_at"))
                if revision:
                    projection_cache.put(cache_key, cached)
                else:
                    cache_state = "bypass"
            tasks, source_error, revision, observed_at = cached
            errors = [source_error] if source_error else []
            has_more = len(tasks) > limit
            page_tasks = tasks[:limit]
            items = [
                {
                    "task_id": item.get("id"),
                    "title": item.get("title"),
                    "priority": item.get("priority"),
                    "status": item.get("status"),
                    "claimed_by": item.get("claimed_by"),
                }
                for item in page_tasks
            ]
            result = _envelope(
                request, "skcoord", items, errors, observed_at=observed_at
            )
            next_offset = offset + len(page_tasks)
            result["page"] = {
                "limit": limit,
                "next_cursor": (
                    encode_page_cursor(next_offset, revision, scope)
                    if has_more and revision
                    else None
                ),
                "has_more": has_more,
            }
            if has_more and not revision:
                return _error(
                    request,
                    503,
                    "SOURCE_UNAVAILABLE",
                    "owner pagination revision is unavailable",
                    retryable=True,
                )
            response = _response(request, result)
            response.headers["X-Projection-Cache"] = cache_state
            return response
        except ValueError as exc:
            return _error(request, 400, "INVALID_QUERY", str(exc))
        except Exception:
            return _error(
                request,
                503,
                "SOURCE_UNAVAILABLE",
                "the bounded board owner is unavailable",
                retryable=True,
            )

    async def fleet(request):
        from . import dashboard_fleet

        try:
            raw = dashboard_fleet.get_drift(home, alert=False)
            errors = [str(value) for value in raw.get("errors", [])]
            items = [
                {
                    "node_id": node.get("node"),
                    "state": node.get("severity", "unknown"),
                    "counts": node.get("counts", {}),
                }
                for node in raw.get("nodes", [])
            ] + [
                {
                    "node_id": node.get("node"),
                    "state": "unknown",
                    "reason_code": node.get("reason_code", "ungraded"),
                }
                for node in raw.get("skipped", [])
            ]
            return _response(request, _page(request, "skcapstone.fleet", items, errors))
        except ValueError as exc:
            return _error(request, 400, "INVALID_QUERY", str(exc))

    async def economy(request):
        from . import dashboard_skcounter

        try:
            lane = request.query_params.get("measurement_lane", "harness_reported")
            if lane not in dashboard_skcounter.LANES:
                raise ValueError("measurement_lane is invalid")
            raw = dashboard_skcounter.get_ai_usage(
                home,
                {"lane": lane, "from": request.query_params.get("from", ""), "to": request.query_params.get("to", "")},
            )
            summary = raw.get("summary", {})
            coverage = raw.get("coverage", {})
            cost = summary.get("cost_usd") if summary.get("cost_state") == "available" else None
            items = [{
                "measurement_lane": raw.get("selected_lane"),
                "available_lanes": raw.get("available_lanes", []),
                "tokens": {key: summary.get(key, 0) for key in dashboard_skcounter.TOKEN_FIELDS},
                "cost_usd": cost,
                "cost_state": summary.get("cost_state", "unavailable"),
                "collectors": raw.get("collectors", []),
                "expected_nodes": coverage.get("expected_nodes", 0),
                "reporting_nodes": coverage.get("reporting_nodes", 0),
                "missing_nodes": coverage.get("missing_nodes", []),
            }]
            return _response(
                request,
                _page(request, "skcounter", items, [str(x) for x in raw.get("errors", [])], observed_at=raw.get("generated_at")),
            )
        except ValueError as exc:
            return _error(request, 400, "INVALID_QUERY", str(exc))

    async def overview(request):
        cache_key = (
            "overview",
            getattr(request.state, "control_plane_cache_scope", "public"),
            tuple(sorted(request.query_params.multi_items())),
        )
        cached = projection_cache.get(cache_key)
        if cached is not None:
            response = _response(request, cached)
            response.headers["X-Projection-Cache"] = "hit"
            return response

        from .control_plane_adapters import default_readers, project_estate
        from .control_plane_quality import project_data_quality
        from .control_plane_scope import (
            ProtectedScopeDenied,
            ScopeQueryError,
            parse_now_scope,
        )

        try:
            scope = parse_now_scope(request.query_params)
        except ProtectedScopeDenied:
            return _error(
                request,
                403,
                "PROTECTED_SCOPE_DENIED",
                "protected scope is not available",
            )
        except ScopeQueryError as exc:
            return _error(request, 400, "INVALID_SCOPE", str(exc))
        from skcoord.authorized_card_snapshot import (
            AuthorizedCardScopeV1,
            unavailable_authorized_card_snapshot,
        )

        adapter_items = project_estate(default_readers(home))
        project_scope = AuthorizedCardScopeV1(
            role=scope.role,
            scope=scope.scope,
            service=scope.service,
            window=scope.window,
            baseline=scope.baseline,
        )
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        project = unavailable_authorized_card_snapshot(project_scope)
        if project_provider is not None and context is not None and verifier is not None:
            project = project_provider.read(
                context,
                project_scope,
                home,
                currentness_verifier=verifier,
            )
        errors = [
            f"{item.get('adapter_id', item.get('projection_type', 'source'))}: {error['code']}"
            for item in [*adapter_items, project]
            for error in item["errors"]
        ]
        states = {item["truth_state"] for item in [*adapter_items, project]}
        truth = (
            "current"
            if states == {"current"}
            else ("unavailable" if states == {"unavailable"} else "partial")
        )
        quality = project_data_quality(adapter_items)
        result = _envelope(
            request,
            "skdashboard",
            [*adapter_items, project, quality],
            errors,
            truth_state=truth,
            scope=scope.as_dict(),
        )
        cache_state = "bypass"
        if truth == "current":
            projection_cache.put(cache_key, result)
            cache_state = "miss"
        response = _response(request, result)
        response.headers["X-Projection-Cache"] = cache_state
        return response

    async def schedule(request):
        allowed = {
            "role", "scope", "window", "baseline", "service", "lens",
            "timezone", "selected_item",
        }
        pairs = list(request.query_params.multi_items())
        if (
            any(key not in allowed or not value or len(value) > 128 for key, value in pairs)
            or len({key for key, _value in pairs}) != len(pairs)
        ):
            return _error(request, 400, "INVALID_SCHEDULE_SCOPE", "unsupported schedule scope")
        query = dict(pairs)
        if (
            query.get("role") not in {"project-manager", "operator", "architect", "service", "team"}
            or query.get("scope") != "estate"
            or query.get("window") != "latest"
            or query.get("baseline") != "none"
            or query.get("service") != "all"
            or query.get("lens") not in {"roadmap", "gantt", "flow"}
            or not query.get("timezone")
        ):
            return _error(request, 400, "INVALID_SCHEDULE_SCOPE", "unsupported schedule scope")
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        if schedule_provider is None or context is None or verifier is None:
            return _error(
                request,
                503,
                "SCHEDULE_UNAVAILABLE",
                "the authorized schedule projection is unavailable",
                retryable=True,
            )
        projection = schedule_provider.read(
            context,
            query,
            home,
            currentness_verifier=verifier,
        )
        if not isinstance(projection, dict):
            return _error(request, 503, "SCHEDULE_UNAVAILABLE", "invalid schedule projection")
        serialized = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
        etag = f'"{hashlib.sha256(serialized).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(serialized, media_type="application/json", headers={"ETag": etag})

    async def reliability(request):
        allowed = {"role", "scope", "window", "baseline", "service"}
        pairs = list(request.query_params.multi_items())
        if (
            any(key not in allowed or not value or len(value) > 128 for key, value in pairs)
            or len({key for key, _value in pairs}) != len(pairs)
        ):
            return _error(
                request,
                400,
                "INVALID_RELIABILITY_SCOPE",
                "unsupported reliability scope",
            )
        query = dict(pairs)
        if (
            query.get("role") not in {"operator", "architect", "service-owner"}
            or query.get("scope") != "estate"
            or query.get("window") != "latest"
            or query.get("baseline") != "none"
            or query.get("service") != "all"
        ):
            return _error(
                request,
                400,
                "INVALID_RELIABILITY_SCOPE",
                "unsupported reliability scope",
            )
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        if reliability_provider is None or context is None or verifier is None:
            return _error(
                request,
                503,
                "RELIABILITY_UNAVAILABLE",
                "the authorized reliability projection is unavailable",
                retryable=True,
            )
        projection = reliability_provider.read(
            context,
            query,
            home,
            currentness_verifier=verifier,
        )
        if not isinstance(projection, dict):
            return _error(
                request,
                503,
                "RELIABILITY_UNAVAILABLE",
                "invalid reliability projection",
            )
        serialized = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
        etag = f'"{hashlib.sha256(serialized).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(serialized, media_type="application/json", headers={"ETag": etag})

    async def architecture(request):
        allowed = {"role", "scope", "window", "baseline", "service", "environment"}
        pairs = list(request.query_params.multi_items())
        if (
            any(key not in allowed or not value or len(value) > 128 for key, value in pairs)
            or len({key for key, _value in pairs}) != len(pairs)
        ):
            return _error(
                request,
                400,
                "INVALID_ARCHITECTURE_SCOPE",
                "unsupported architecture scope",
            )
        query = dict(pairs)
        if (
            query.get("role") not in {"architect", "operator", "service-owner"}
            or query.get("scope") != "estate"
            or query.get("window") != "latest"
            or query.get("baseline") != "none"
            or query.get("service") != "all"
            or query.get("environment") != "all"
        ):
            return _error(
                request,
                400,
                "INVALID_ARCHITECTURE_SCOPE",
                "unsupported architecture scope",
            )
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        if architecture_provider is None or context is None or verifier is None:
            return _error(
                request,
                503,
                "ARCHITECTURE_UNAVAILABLE",
                "the authorized architecture projection is unavailable",
                retryable=True,
            )
        projection = architecture_provider.read(
            context,
            query,
            home,
            currentness_verifier=verifier,
        )
        if not isinstance(projection, dict):
            return _error(
                request,
                503,
                "ARCHITECTURE_UNAVAILABLE",
                "invalid architecture projection",
            )
        serialized = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
        etag = f'"{hashlib.sha256(serialized).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(serialized, media_type="application/json", headers={"ETag": etag})

    async def governance(request):
        allowed = {"role", "scope", "window", "baseline", "service"}
        pairs = list(request.query_params.multi_items())
        if (
            any(key not in allowed or not value or len(value) > 128 for key, value in pairs)
            or len({key for key, _value in pairs}) != len(pairs)
        ):
            return _error(
                request,
                400,
                "INVALID_GOVERNANCE_SCOPE",
                "unsupported governance scope",
            )
        query = dict(pairs)
        if (
            query.get("role") not in {"governance", "auditor", "operator"}
            or query.get("scope") != "estate"
            or query.get("window") != "latest"
            or query.get("baseline") != "none"
            or query.get("service") != "all"
        ):
            return _error(
                request,
                400,
                "INVALID_GOVERNANCE_SCOPE",
                "unsupported governance scope",
            )
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        if governance_provider is None or context is None or verifier is None:
            return _error(
                request,
                503,
                "GOVERNANCE_UNAVAILABLE",
                "the authorized governance projection is unavailable",
                retryable=True,
            )
        projection = governance_provider.read(
            context,
            query,
            home,
            currentness_verifier=verifier,
        )
        if not isinstance(projection, dict):
            return _error(
                request,
                503,
                "GOVERNANCE_UNAVAILABLE",
                "invalid governance projection",
            )
        serialized = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
        etag = f'"{hashlib.sha256(serialized).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(serialized, media_type="application/json", headers={"ETag": etag})

    async def reports(request):
        allowed = {"role", "scope", "window", "baseline", "service", "report_type", "snapshot", "compare"}
        pairs = list(request.query_params.multi_items())
        if (
            any(key not in allowed or not value or len(value) > 128 for key, value in pairs)
            or len({key for key, _value in pairs}) != len(pairs)
        ):
            return _error(request, 400, "INVALID_REPORT_SCOPE", "unsupported report scope")
        query = dict(pairs)
        if (
            query.get("role") not in {"project-manager", "operator", "architect", "auditor"}
            or query.get("scope") != "estate"
            or query.get("window") != "latest"
            or query.get("baseline") not in {"none", "previous"}
            or query.get("service") != "all"
            or query.get("report_type", "all")
            not in {
                "all", "daily_operations", "weekly_portfolio", "sprint_flow",
                "monthly_service", "monthly_ai_economy", "quarterly_strategy",
                "ad_hoc_evidence",
            }
        ):
            return _error(request, 400, "INVALID_REPORT_SCOPE", "unsupported report scope")
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        if report_provider is None or context is None or verifier is None:
            return _error(request, 503, "REPORTS_UNAVAILABLE", "the authorized report projection is unavailable", retryable=True)
        try:
            projection = report_provider.read(
                context, query, home, currentness_verifier=verifier
            )
        except KeyError:
            return _error(request, 404, "REPORT_NOT_FOUND", "the immutable report snapshot was not found")
        except ValueError:
            return _error(request, 503, "REPORTS_UNAVAILABLE", "the immutable report store is unavailable", retryable=True)
        if not isinstance(projection, dict):
            return _error(request, 503, "REPORTS_UNAVAILABLE", "invalid report projection")
        serialized = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
        etag = f'"{hashlib.sha256(serialized).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(serialized, media_type="application/json", headers={"ETag": etag})

    async def report_snapshot(request):
        snapshot_id = request.path_params.get("snapshot_id", "")
        if len(snapshot_id) > 96:
            return _error(request, 404, "REPORT_NOT_FOUND", "the immutable report snapshot was not found")
        context = getattr(request.state, "control_plane_decision", None)
        verifier = getattr(request.state, "control_plane_currentness_verifier", None)
        if report_provider is None or context is None or verifier is None:
            return _error(request, 503, "REPORTS_UNAVAILABLE", "the authorized report snapshot is unavailable", retryable=True)
        try:
            snapshot = report_provider.read_snapshot(
                context, snapshot_id, home, currentness_verifier=verifier
            )
        except KeyError:
            return _error(request, 404, "REPORT_NOT_FOUND", "the immutable report snapshot was not found")
        except ValueError:
            return _error(request, 404, "REPORT_NOT_FOUND", "the immutable report snapshot was not found")
        if not isinstance(snapshot, dict):
            return _error(request, 503, "REPORTS_UNAVAILABLE", "invalid report snapshot")
        serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        etag = f'"{snapshot["report_hash"].removeprefix("sha256:")}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(serialized, media_type="application/json", headers={"ETag": etag})

    async def events(request):
        raw_cursor = request.query_params.get("cursor") or request.headers.get("last-event-id")
        topics = [value for value in request.query_params.get("topics", "").split(",") if value]
        try:
            if len(topics) > 16 or any(len(value) > 64 for value in topics):
                raise ValueError("topics exceed the bounded contract")
            if raw_cursor:
                _cursor(raw_cursor)
        except ValueError as exc:
            return _error(request, 400, "INVALID_QUERY", str(exc))

        async def stream():
            if raw_cursor:
                yield "event: reset-required\ndata: {\"reason\":\"replay window unavailable\"}\n\n"
            yield ": heartbeat\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def metrics(_request):
        lines = [
            "# HELP skdashboard_control_plane_up Whether this projection process is serving.",
            "# TYPE skdashboard_control_plane_up gauge",
            "skdashboard_control_plane_up 1",
            "# HELP skdashboard_control_plane_requests_total Bounded control-plane requests.",
            "# TYPE skdashboard_control_plane_requests_total counter",
            f"skdashboard_control_plane_requests_total {counters['requests']}",
            "# HELP skdashboard_control_plane_denied_total Denied control-plane requests.",
            "# TYPE skdashboard_control_plane_denied_total counter",
            f"skdashboard_control_plane_denied_total {counters['denied']}",
            "# HELP skdashboard_control_plane_rate_requests_total Bounded requests by isolation class and result.",
            "# TYPE skdashboard_control_plane_rate_requests_total counter",
            *[
                f'skdashboard_control_plane_rate_requests_total{{class="{rate_class}",result="{result}"}} {values[rate_class]}'
                for rate_class in ("common", "critical")
                for result, values in (
                    ("accepted", counters["rate_accepted"]),
                    ("rejected", counters["rate_rejected"]),
                )
            ],
            "# HELP skdashboard_control_plane_projection_cache_entries Bounded cached owner pages.",
            "# TYPE skdashboard_control_plane_projection_cache_entries gauge",
            f"skdashboard_control_plane_projection_cache_entries {projection_cache.size}",
            "# HELP skdashboard_control_plane_projection_cache_events_total Projection cache events.",
            "# TYPE skdashboard_control_plane_projection_cache_events_total counter",
            f'skdashboard_control_plane_projection_cache_events_total{{event="hit"}} {projection_cache.hits}',
            f'skdashboard_control_plane_projection_cache_events_total{{event="miss"}} {projection_cache.misses}',
            f'skdashboard_control_plane_projection_cache_events_total{{event="invalidation"}} {projection_cache.invalidations}',
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    return [
        Route("/api/v1/health", limited(health, "critical")),
        Route("/api/v1/overview", protected(overview, "skdashboard.read")),
        Route("/api/v1/schedule/projection", protected(schedule, "skdashboard.read")),
        Route("/api/v1/reliability/projection", protected(reliability, "skdashboard.read")),
        Route("/api/v1/architecture/projection", protected(architecture, "skdashboard.read")),
        Route("/api/v1/governance/projection", protected(governance, "skdashboard.read")),
        Route("/api/v1/reports/projection", protected(reports, "skdashboard.read")),
        Route(
            "/api/v1/reports/{snapshot_id}",
            protected(
                report_snapshot, "skdashboard.reports.read", rate_class="critical"
            ),
        ),
        Route("/api/v1/board/summary", protected(board, "skdashboard.read")),
        Route("/api/v1/fleet/summary", protected(fleet, "skdashboard.read")),
        Route("/api/v1/economy/summary", protected(economy, "skdashboard.read")),
        Route("/api/v1/events", protected(events, "skdashboard.events.read")),
        Route(
            "/metrics", protected(metrics, "skdashboard.read", rate_class="critical")
        ),
    ]
