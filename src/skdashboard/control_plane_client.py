"""Allowlisted, schema-validating client for the frozen control-plane API."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import AsyncIterator, Mapping
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACT_VERSION = "1.1.0"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_EVENT_TOPICS = 16
REPORT_ID = re.compile(r"^rpt-[a-z0-9][a-z0-9-]{7,95}$")
METRIC_FAMILIES = frozenset(
    {
        "portfolio",
        "flow",
        "reliability",
        "delivery",
        "architecture",
        "ai",
        "economy",
        "governance",
        "experience",
    }
)
SCOPE_KEYS = frozenset({"project_id", "service_id", "environment", "baseline"})
PAGE_OPERATIONS = frozenset({"board", "fleet", "economy"})
ACTION_CAPABILITIES = frozenset(
    {"skdashboard.actions.preview", "skdashboard.actions.authorize"}
)
PREVIEW_ID = re.compile(r"^apv-[a-z0-9][a-z0-9-]{7,95}$")
RECEIPT_ID = re.compile(r"^cmdr-[a-z0-9][a-z0-9-]{7,95}$")
HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
MANIFEST_HASH = re.compile(r"^[a-f0-9]{64}$")

_OPERATIONS = {
    "health": ("GET", "/api/v1/health", "envelope", False),
    "overview": ("GET", "/api/v1/overview", "envelope", True),
    "board": ("GET", "/api/v1/board/summary", "envelope", True),
    "fleet": ("GET", "/api/v1/fleet/summary", "envelope", True),
    "economy": ("GET", "/api/v1/economy/summary", "envelope", True),
    "insight": ("POST", "/api/v1/insights/query", "insight", True),
    "preview_action": ("POST", "/api/v1/actions/preview", "action_preview", True),
    "authorize_action": ("POST", None, "receipt", True),
    "receipt": ("GET", None, "receipt", True),
}


class ControlPlaneClientError(RuntimeError):
    """A response failed origin, transport, status, or schema validation."""


@dataclass(frozen=True)
class ClientResponse:
    data: dict
    etag: str | None
    not_modified: bool = False


@dataclass(frozen=True)
class _PreviewBinding:
    preview_id: str
    preview_hash: str
    status: str
    expires_at: str
    target: tuple[tuple[str, str], ...]
    parameters: tuple[str, str, str]


def canonical_manifest_hash(manifest: Mapping[str, object]) -> str:
    """Hash the canonical manifest, excluding its caller-verifiable binding."""
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    encoded = json.dumps(unsigned, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reject_protected(value: object) -> None:
    """Reject protected Matter aliases without including the value in errors."""
    aliases = {
        "tenant",
        "tenantid",
        "tenantref",
        "tenantreference",
        "matter",
        "matterid",
        "matterref",
        "matterreference",
        "matterpayload",
        "protectedmatter",
    }

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if normalized in aliases:
                    raise ControlPlaneClientError("MCP resource contains protected fields")
                if normalized in {"classification", "actionclass"} and isinstance(child, str):
                    if re.sub(r"[^a-z0-9]", "", child.lower()) == "protectedmatter":
                        raise ControlPlaneClientError("MCP resource contains protected fields")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            if re.search(r"(?:^|[^a-z])protected[_ -]?matter(?:$|[^a-z])", item, re.I):
                raise ControlPlaneClientError("MCP resource contains protected fields")
            if re.search(r"\bclassified\s+matter\s+summary\b", item, re.I):
                raise ControlPlaneClientError("MCP resource contains protected fields")
            if re.search(r"(?:^|[^a-z])matter(?:[-_:/.]|\d)", item, re.I):
                raise ControlPlaneClientError("MCP resource contains protected fields")

    visit(value)


def _contract_documents() -> dict[str, dict]:
    root = Path(__file__).parent / "contracts" / "v1.1.0"
    names = (
        "openapi.control-plane.v1.1.0.json",
        "control-plane-metric-result.v1.1.0.schema.json",
        "control-plane-recommendation.v1.1.0.schema.json",
        "control-plane-action-preview.v1.1.0.schema.json",
        "control-plane-insight.v1.1.0.schema.json",
        "control-plane-report-snapshot.v1.1.0.schema.json",
    )
    return {name: json.loads((root / name).read_text(encoding="utf-8")) for name in names}


class ContractValidators:
    """Load the published schemas once and validate exact response families."""

    def __init__(self) -> None:
        documents = _contract_documents()
        resources = []
        for document in documents.values():
            if schema_id := document.get("$id"):
                resources.append((schema_id, Resource.from_contents(document)))
        self.registry = Registry().with_resources(resources)
        self.format_checker = FormatChecker()
        openapi = copy.deepcopy(documents["openapi.control-plane.v1.1.0.json"])
        openapi.update(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://schemas.skworld.local/skdashboard/openapi.control-plane.v1.1.0.json",
                "$ref": "#/components/schemas/ProjectionEnvelope",
            }
        )
        self.validators = {
            "envelope": Draft202012Validator(
                openapi, registry=self.registry, format_checker=self.format_checker
            ),
            "insight": Draft202012Validator(
                documents["control-plane-insight.v1.1.0.schema.json"],
                registry=self.registry,
                format_checker=self.format_checker,
            ),
            "report": Draft202012Validator(
                documents["control-plane-report-snapshot.v1.1.0.schema.json"],
                registry=self.registry,
                format_checker=self.format_checker,
            ),
        }
        action = copy.deepcopy(documents["openapi.control-plane.v1.1.0.json"])
        action.update(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://schemas.skworld.local/skdashboard/action-preview.v1.1.0.json",
                "$ref": "https://schemas.skworld.local/skdashboard/control-plane-action-preview.v1.1.0.schema.json",
            }
        )
        self.validators["action_preview"] = Draft202012Validator(
            action, registry=self.registry, format_checker=self.format_checker
        )
        receipt = copy.deepcopy(documents["openapi.control-plane.v1.1.0.json"])
        receipt.update(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://schemas.skworld.local/skdashboard/command-receipt.v1.1.0.json",
                "$ref": "#/components/schemas/CommandReceipt",
            }
        )
        self.validators["receipt"] = Draft202012Validator(
            receipt, registry=self.registry, format_checker=self.format_checker
        )
        query = copy.deepcopy(documents["openapi.control-plane.v1.1.0.json"])
        query.update(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://schemas.skworld.local/skdashboard/insight-query.v1.1.0.json",
                "$ref": "#/components/schemas/InsightQuery",
            }
        )
        self.validators["insight_query"] = Draft202012Validator(
            query, registry=self.registry, format_checker=self.format_checker
        )
        error = copy.deepcopy(documents["openapi.control-plane.v1.1.0.json"])
        error.update(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://schemas.skworld.local/skdashboard/error.v1.1.0.json",
                "$ref": "#/components/schemas/Error",
            }
        )
        self.validators["error"] = Draft202012Validator(
            error, registry=self.registry, format_checker=self.format_checker
        )

    def validate(self, family: str, value: object) -> None:
        try:
            self.validators[family].validate(value)
        except Exception as error:
            raise ControlPlaneClientError(f"{family} response failed schema validation") from error


class ControlPlaneClient:
    """Read only the frozen allowlist from one discovered same-origin API."""

    def __init__(
        self,
        origin: str,
        bearer: str,
        http: httpx.AsyncClient,
        *,
        manifest: Mapping[str, object],
        owns_http: bool,
        validators: ContractValidators | None = None,
    ) -> None:
        self.origin = origin.rstrip("/")
        self._bearer = bearer
        self._http = http
        self._owns_http = owns_http
        self.manifest = copy.deepcopy(dict(manifest))
        self.validators = validators or ContractValidators()
        self._cache: dict[str, ClientResponse] = {}
        self._previews: dict[str, _PreviewBinding] = {}
        self._consumed_previews: set[tuple[str, str]] = set()
        self._preview_lock = Lock()
        self._receipts: dict[str, tuple[str, str]] = {}

    def __repr__(self) -> str:
        return f"ControlPlaneClient(origin={self.origin!r})"

    @classmethod
    async def discover(
        cls,
        discovery_url: str,
        bearer: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 5.0,
        manifest_sha256: str | None = None,
    ) -> "ControlPlaneClient":
        parsed = urlsplit(discovery_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/.well-known/skworld-module.json"
            or len(discovery_url) > 2048
        ):
            raise ControlPlaneClientError("discovery URL is not a canonical HTTPS manifest")
        if not bearer or len(bearer.encode("utf-8")) > 64 * 1024:
            raise ControlPlaneClientError("bearer is missing or exceeds its bound")
        http = httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)
        try:
            response = await http.get(discovery_url, headers={"Accept": "application/json"})
            if response.status_code != 200 or len(response.content) > 64 * 1024:
                raise ControlPlaneClientError("control-plane discovery failed")
            manifest = response.json()
            origin = f"{parsed.scheme}://{parsed.netloc}"
            cls._validate_manifest(manifest, origin, manifest_sha256)
            return cls(origin, bearer, http, manifest=manifest, owns_http=True)
        except Exception:
            await http.aclose()
            raise

    @staticmethod
    def _validate_manifest(
        manifest: object, origin: str, approved_hash: str | None
    ) -> None:
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != "1.1":
            raise ControlPlaneClientError("discovery manifest is incompatible")
        auth = manifest.get("auth")
        if not isinstance(auth, dict) or auth.get("audience") != "skdashboard":
            raise ControlPlaneClientError("discovery manifest has the wrong audience")
        health = manifest.get("health")
        if health != origin + "/api/v1/health":
            raise ControlPlaneClientError("discovery manifest has no canonical health route")
        entry = manifest.get("entry")
        entry_url = entry.get("url") if isinstance(entry, dict) else None
        parsed_entry = urlsplit(entry_url) if isinstance(entry_url, str) else None
        if parsed_entry is None or entry_url.rstrip("/") != origin:
            raise ControlPlaneClientError("discovery entry crosses origins")
        if (
            not isinstance(approved_hash, str)
            or not MANIFEST_HASH.fullmatch(approved_hash)
            or manifest.get("manifest_sha256") != approved_hash
            or not hmac.compare_digest(canonical_manifest_hash(manifest), approved_hash)
        ):
            raise ControlPlaneClientError("discovery manifest is not caller-pinned")

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "ControlPlaneClient":
        return self

    async def __aexit__(self, *_args) -> None:
        await self.aclose()

    @staticmethod
    def _query(values: Mapping[str, object] | None, allowed: frozenset[str]) -> dict[str, str]:
        query = {}
        for key, value in (values or {}).items():
            if key not in allowed or not isinstance(value, str) or not value or len(value) > 512:
                raise ControlPlaneClientError("query is outside the frozen allowlist")
            query[key] = value
        return query

    async def _request(
        self,
        operation: str,
        *,
        params: Mapping[str, object] | None = None,
        body: dict | None = None,
        path: str | None = None,
        schema: str | None = None,
        protected: bool | None = None,
    ) -> ClientResponse:
        if operation in {"authorize_action", "receipt"} and path is not None:
            method, fixed_path, family, requires_auth = (
                _OPERATIONS[operation][0],
                path,
                _OPERATIONS[operation][2],
                _OPERATIONS[operation][3],
            )
        elif operation in _OPERATIONS:
            method, fixed_path, family, requires_auth = _OPERATIONS[operation]
        elif operation == "report" and path is not None:
            method, fixed_path, family, requires_auth = "GET", path, "report", True
        else:
            raise ControlPlaneClientError("operation is not allowlisted")
        family = schema or family
        requires_auth = requires_auth if protected is None else protected
        url = self.origin + fixed_path
        headers = {"Accept": "application/json"}
        if requires_auth:
            headers["Authorization"] = f"Bearer {self._bearer}"
        cache_key = json.dumps([method, fixed_path, params or {}], sort_keys=True)
        if method == "GET" and cache_key in self._cache and self._cache[cache_key].etag:
            headers["If-None-Match"] = self._cache[cache_key].etag or ""
        encoded = None
        if body is not None:
            _reject_protected(body)
            if operation == "insight":
                self.validators.validate("insight_query", body)
            encoded = json.dumps(body, allow_nan=False, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise ControlPlaneClientError("request body exceeds its bound")
            headers["Content-Type"] = "application/json"
        response = await self._http.request(
            method, url, params=dict(params or {}), content=encoded, headers=headers
        )
        if response.status_code == 304:
            cached = self._cache.get(cache_key)
            if cached is None:
                raise ControlPlaneClientError("server returned 304 without a validated baseline")
            return ClientResponse(copy.deepcopy(cached.data), cached.etag, True)
        expected_statuses = {200, 202} if operation == "authorize_action" else {200}
        if response.status_code not in expected_statuses:
            try:
                error = response.json()
            except ValueError as exc:
                raise ControlPlaneClientError("error response is not JSON") from exc
            self.validators.validate("error", error)
            raise ControlPlaneClientError(
                f"control-plane request failed: {response.status_code} {error['code']}"
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ControlPlaneClientError("response exceeds its read bound")
        if not response.headers.get("content-type", "").startswith("application/json"):
            raise ControlPlaneClientError("response media type is not JSON")
        try:
            data = response.json()
        except ValueError as error:
            raise ControlPlaneClientError("response is not JSON") from error
        self.validators.validate(family, data)
        if family in {"action_preview", "receipt"}:
            _reject_protected(data)
        result = ClientResponse(copy.deepcopy(data), response.headers.get("etag"))
        if method == "GET":
            self._cache[cache_key] = ClientResponse(copy.deepcopy(data), result.etag)
        return result

    async def health(self) -> ClientResponse:
        return await self._request("health")

    async def overview(self, scope: Mapping[str, object] | None = None) -> ClientResponse:
        return await self._request("overview", params=self._query(scope, SCOPE_KEYS))

    async def saved_scope(self, scope: Mapping[str, object]) -> ClientResponse:
        return await self.overview(scope)

    async def board(self, **query: str) -> ClientResponse:
        return await self._request(
            "board",
            params=self._query(
                query, frozenset({"project_id", "from", "to", "timezone", "cursor", "limit"})
            ),
        )

    async def fleet(self, **query: str) -> ClientResponse:
        return await self._request(
            "fleet", params=self._query(query, frozenset({"environment", "cursor", "limit"}))
        )

    async def economy(self, **query: str) -> ClientResponse:
        return await self._request(
            "economy",
            params=self._query(
                query,
                frozenset(
                    {"project_id", "measurement_lane", "from", "to", "timezone", "cursor", "limit"}
                ),
            ),
        )

    async def report(self, snapshot_id: str) -> ClientResponse:
        if not REPORT_ID.fullmatch(snapshot_id):
            raise ControlPlaneClientError("report snapshot id is invalid")
        return await self._request("report", path=f"/api/v1/reports/{snapshot_id}")

    async def insight(self, query: dict) -> ClientResponse:
        return await self._request("insight", body=query)

    @staticmethod
    def _capability(capability: str, expected: str) -> None:
        if capability != expected or capability not in ACTION_CAPABILITIES:
            raise ControlPlaneClientError("explicit action capability is required")

    async def preview_action(
        self,
        recommendation_id: str,
        action_contract_id: str,
        parameter_proposal_ref: str,
        *,
        capability: str,
    ) -> ClientResponse:
        self._capability(capability, "skdashboard.actions.preview")
        if not recommendation_id or len(recommendation_id) > 96:
            raise ControlPlaneClientError("recommendation id is invalid")
        if not action_contract_id or len(action_contract_id) > 128:
            raise ControlPlaneClientError("action contract id is invalid")
        if not parameter_proposal_ref or len(parameter_proposal_ref) > 512:
            raise ControlPlaneClientError("parameter proposal reference is invalid")
        response = await self._request(
            "preview_action",
            body={
                "recommendation_id": recommendation_id,
                "action_contract_id": action_contract_id,
                "parameter_proposal_ref": parameter_proposal_ref,
            },
        )
        preview = response.data
        if (
            preview["source_recommendation_id"] != recommendation_id
            or preview["action_contract_id"] != action_contract_id
        ):
            raise ControlPlaneClientError("action preview does not match request")
        self._previews[preview["preview_id"]] = _PreviewBinding(
            preview_id=preview["preview_id"],
            preview_hash=preview["preview_hash"],
            status=preview["status"],
            expires_at=preview["expires_at"],
            target=tuple(sorted(preview["target"].items())),
            parameters=(recommendation_id, action_contract_id, parameter_proposal_ref),
        )
        return response

    async def submit_action(
        self,
        preview_id: str,
        preview_hash: str,
        idempotency_key: str,
        approval_reason: str,
        *,
        capability: str,
    ) -> ClientResponse:
        self._capability(capability, "skdashboard.actions.authorize")
        if not PREVIEW_ID.fullmatch(preview_id) or not HASH.fullmatch(preview_hash):
            raise ControlPlaneClientError("action preview identity is invalid")
        binding = self._previews.get(preview_id)
        if binding is None or not hmac.compare_digest(binding.preview_hash, preview_hash):
            raise ControlPlaneClientError("action preview is unknown or changed")
        try:
            expires_at = datetime.fromisoformat(binding.expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ControlPlaneClientError("action preview expiry is invalid") from error
        if (
            expires_at.tzinfo is None
            or binding.status != "ready"
            or expires_at <= datetime.now(timezone.utc)
        ):
            raise ControlPlaneClientError("action preview is stale or expired")
        if len(idempotency_key) < 16 or len(idempotency_key) > 128:
            raise ControlPlaneClientError("idempotency key is invalid")
        if not approval_reason or len(approval_reason) > 1000:
            raise ControlPlaneClientError("approval reason is invalid")
        consumed = (preview_id, preview_hash)
        with self._preview_lock:
            if consumed in self._consumed_previews:
                raise ControlPlaneClientError("action preview has already been used")
            self._consumed_previews.add(consumed)
        response = await self._request(
            "authorize_action",
            path=f"/api/v1/action-previews/{preview_id}/authorize",
            body={
                "preview_hash": preview_hash,
                "idempotency_key": idempotency_key,
                "approval_reason": approval_reason,
            },
            schema="receipt",
        )
        receipt = response.data
        if (
            receipt["preview_id"] != preview_id
            or not hmac.compare_digest(receipt["preview_hash"], preview_hash)
        ):
            raise ControlPlaneClientError("receipt is not bound to the submitted preview")
        self._receipts[receipt["receipt_id"]] = (preview_id, preview_hash)
        return response

    async def poll_receipt(
        self,
        receipt_id: str,
        *,
        timeout: float = 10.0,
        interval: float = 0.05,
        cancel_event: asyncio.Event | None = None,
    ) -> ClientResponse:
        if not RECEIPT_ID.fullmatch(receipt_id):
            raise ControlPlaneClientError("receipt id is invalid")
        binding = self._receipts.get(receipt_id)
        if binding is None:
            raise ControlPlaneClientError("receipt id is unknown")
        if timeout <= 0 or interval <= 0 or interval > timeout:
            raise ControlPlaneClientError("receipt polling bounds are invalid")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            response = await self._request(
                "receipt",
                path=f"/api/v1/action-receipts/{receipt_id}",
                schema="receipt",
            )
            if response.data.get("verification_state") != "pending":
                if (
                    response.data.get("receipt_id") != receipt_id
                    or response.data.get("preview_id") != binding[0]
                    or response.data.get("preview_hash") != binding[1]
                ):
                    raise ControlPlaneClientError("receipt identity does not match request")
                return response
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("receipt polling timed out")
            await asyncio.sleep(min(interval, remaining))

    async def metric_family(
        self, family: str, scope: Mapping[str, object] | None = None
    ) -> list[dict]:
        if family not in METRIC_FAMILIES:
            raise ControlPlaneClientError("metric family is not allowlisted")
        response = await (self.economy() if family == "economy" else self.overview(scope))
        return [
            copy.deepcopy(metric)
            for metric in response.data.get("metrics", [])
            if str(metric.get("metric_id", "")).startswith(f"{family}.")
        ]

    async def pages(
        self, operation: str, *, max_pages: int = 100, **query: str
    ) -> AsyncIterator[ClientResponse]:
        if operation not in PAGE_OPERATIONS or not 1 <= max_pages <= 100:
            raise ControlPlaneClientError("pagination request is outside its bound")
        cursor = query.pop("cursor", None)
        seen = set()
        for _ in range(max_pages):
            if cursor:
                if cursor in seen or len(cursor) > 512:
                    raise ControlPlaneClientError(
                        "pagination cursor repeated or exceeded its bound"
                    )
                seen.add(cursor)
                query["cursor"] = cursor
            response = await getattr(self, operation)(**query)
            yield response
            page = response.data.get("page") or {}
            cursor = page.get("next_cursor")
            if not page.get("has_more"):
                return
        raise ControlPlaneClientError("pagination exceeded its page bound")

    async def events(
        self, *, cursor: str | None = None, topics: tuple[str, ...] = ()
    ) -> list[dict]:
        if cursor is not None and (not cursor or len(cursor) > 512):
            raise ControlPlaneClientError("event cursor is invalid")
        if len(topics) > MAX_EVENT_TOPICS or any(not value or len(value) > 64 for value in topics):
            raise ControlPlaneClientError("event topics exceed their bound")
        params = {"topics": ",".join(topics)} if topics else {}
        if cursor:
            params["cursor"] = cursor
        response = await self._http.get(
            self.origin + "/api/v1/events",
            params=params,
            headers={"Accept": "text/event-stream", "Authorization": f"Bearer {self._bearer}"},
        )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            raise ControlPlaneClientError("event response failed or exceeded its bound")
        Draft202012Validator({"type": "string", "maxLength": MAX_RESPONSE_BYTES}).validate(
            response.text
        )
        events = []
        for block in response.text.split("\n\n"):
            lines = [line for line in block.splitlines() if line and not line.startswith(":")]
            if not lines:
                continue
            event = next(
                (line[6:] for line in lines if line.startswith("event:")), "message"
            ).strip()
            data = "\n".join(line[5:].lstrip() for line in lines if line.startswith("data:"))
            events.append({"event": event, "data": json.loads(data) if data else None})
        return events

    @staticmethod
    def evidence_refs(document: Mapping[str, object]) -> list[str]:
        found = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "evidence_ref" and isinstance(child, str):
                        found.add(child)
                    elif key == "evidence_refs" and isinstance(child, list):
                        found.update(item for item in child if isinstance(item, str))
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(document)
        if len(found) > 256 or any(not value or len(value) > 512 for value in found):
            raise ControlPlaneClientError("evidence references exceed their bound")
        return sorted(found)


__all__ = [
    "canonical_manifest_hash",
    "ClientResponse",
    "ContractValidators",
    "ControlPlaneClient",
    "ControlPlaneClientError",
]
