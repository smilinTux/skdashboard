"""Allowlisted, schema-validating client for the frozen control-plane API."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
_ACTION_OPERATIONS = frozenset({"preview", "authorize", "receipt", "verify", "cancel", "rollback"})
_ACTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ACTION_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PREVIEW_ID = re.compile(r"^apv-[a-z0-9][a-z0-9-]{7,95}$")
_ACTION_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ACTION_CAPABILITY = re.compile(r"^skdashboard\.[A-Za-z0-9._:-]+$")
_ACTION_TEMPLATE = re.compile(r"\{[^{}]+\}")
_ACTION_SAFE_PARAMETERS = frozenset({"recommendation_id", "parameter_proposal_ref"})
_ACTION_ENDPOINT_TEMPLATES = {
    "preview": "/api/v1/actions/preview",
    "authorize": "/api/v1/action-previews/{preview_id}/authorize",
    "receipt": "/api/v1/action-receipts/{receipt_id}",
    "verify": "/api/v1/action-receipts/{receipt_id}/verify",
    "cancel": "/api/v1/action-receipts/{receipt_id}/cancel",
    "rollback": "/api/v1/action-receipts/{receipt_id}/rollback",
}
_ACTION_PLACEHOLDERS = {
    "preview": frozenset(),
    "authorize": frozenset({"{preview_id}"}),
    "receipt": frozenset({"{receipt_id}"}),
    "verify": frozenset({"{receipt_id}"}),
    "cancel": frozenset({"{receipt_id}"}),
    "rollback": frozenset({"{receipt_id}"}),
}
_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_DANGEROUS_OUTPUT_KEY = re.compile(
    r"(?:authorization|bearer|token|secret|credential|password|tenant|matter|raw|content|endpoint|arbitrary)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)


def _expired(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return expiry <= datetime.now(timezone.utc)


def _materialized_action_path(operation: str, identifier: str, path: str) -> bool:
    template = _ACTION_ENDPOINT_TEMPLATES.get(operation)
    if template is None:
        return False
    placeholder = "{preview_id}" if operation == "authorize" else "{receipt_id}"
    return path == template.replace(placeholder, identifier)


def _reject_unsafe_output(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _DANGEROUS_OUTPUT_KEY.search(str(key)) or _BEARER_VALUE.search(str(child)):
                raise ActionClientError("action response contains protected or credential-like output")
            _reject_unsafe_output(child)
    elif isinstance(value, list):
        for child in value:
            _reject_unsafe_output(child)

_OPERATIONS = {
    "health": ("GET", "/api/v1/health", "envelope", False),
    "overview": ("GET", "/api/v1/overview", "envelope", True),
    "board": ("GET", "/api/v1/board/summary", "envelope", True),
    "fleet": ("GET", "/api/v1/fleet/summary", "envelope", True),
    "economy": ("GET", "/api/v1/economy/summary", "envelope", True),
    "insight": ("POST", "/api/v1/insights/query", "insight", True),
}


class ControlPlaneClientError(RuntimeError):
    """A response failed origin, transport, status, or schema validation."""


@dataclass(frozen=True)
class ClientResponse:
    data: dict
    etag: str | None
    not_modified: bool = False


@dataclass(frozen=True)
class ActionContract:
    """One versioned action surface advertised by discovery."""

    contract_id: str
    version: str
    operations: Mapping[str, str]
    capabilities: Mapping[str, str]
    parameter_fields: frozenset[str] = frozenset()


class ActionClientError(ControlPlaneClientError):
    """An action was not declared, no longer matches, or was rejected."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _contract_documents() -> dict[str, dict]:
    root = Path(__file__).parent / "contracts" / "v1.1.0"
    names = (
        "openapi.control-plane.v1.1.0.json",
        "control-plane-metric-result.v1.1.0.schema.json",
        "control-plane-recommendation.v1.1.0.schema.json",
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
        self._action_contracts = self._load_action_contracts(self.manifest)
        self._previews: dict[str, dict] = {}
        self._receipts: dict[str, dict] = {}

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
            cls._validate_manifest(manifest, origin)
            return cls(origin, bearer, http, manifest=manifest, owns_http=True)
        except Exception:
            await http.aclose()
            raise

    @staticmethod
    def _load_action_contracts(manifest: Mapping[str, object]) -> dict[str, ActionContract]:
        raw = manifest.get("actions")
        if raw is None:
            return {}
        if not isinstance(raw, list) or len(raw) > 64:
            raise ControlPlaneClientError("discovery actions are invalid")
        contracts = {}
        for item in raw:
            if not isinstance(item, dict) or not {"contract_id", "version", "operations", "capabilities"}.issubset(item) or set(item) - {"contract_id", "version", "operations", "capabilities", "parameter_fields"}:
                raise ControlPlaneClientError("discovery action contract is invalid")
            contract_id = item["contract_id"]
            version = item["version"]
            operations = item["operations"]
            capabilities = item["capabilities"]
            if (
                not isinstance(contract_id, str) or not _ACTION_ID.fullmatch(contract_id)
                or not isinstance(version, str) or not _ACTION_VERSION.fullmatch(version)
                or not isinstance(operations, dict) or not isinstance(capabilities, dict)
                or not _ACTION_OPERATIONS.issuperset(operations)
                or set(operations) != set(capabilities)
                or any(not ControlPlaneClient._valid_action_path(operation, value) for operation, value in operations.items())
                or any(
                    not isinstance(value, str)
                    or not _ACTION_CAPABILITY.fullmatch(value)
                    or value != f"skdashboard.actions.{operation}"
                    for operation, value in capabilities.items()
                )
                or not isinstance(item.get("parameter_fields", []), list)
                or any(not isinstance(value, str) or not _ACTION_ID.fullmatch(value) for value in item.get("parameter_fields", []))
                or not set(item.get("parameter_fields", [])).issubset(_ACTION_SAFE_PARAMETERS)
            ):
                raise ControlPlaneClientError("discovery action contract is invalid")
            if contract_id in contracts:
                raise ControlPlaneClientError("discovery action contracts are duplicated")
            fields = frozenset(item.get("parameter_fields", []))
            if not fields:
                fields = frozenset({"recommendation_id", "action_contract_id", "parameter_proposal_ref"})
            contracts[contract_id] = ActionContract(contract_id, version, dict(operations), dict(capabilities), fields)
        return contracts

    @staticmethod
    def _valid_action_path(operation: object, path: object) -> bool:
        if not isinstance(operation, str) or operation not in _ACTION_OPERATIONS or not isinstance(path, str):
            return False
        return path == _ACTION_ENDPOINT_TEMPLATES[operation]

    @staticmethod
    def _validate_manifest(manifest: object, origin: str) -> None:
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
        if operation in _OPERATIONS:
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
        if response.status_code != 200:
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
        result = ClientResponse(copy.deepcopy(data), response.headers.get("etag"))
        if method == "GET":
            self._cache[cache_key] = ClientResponse(copy.deepcopy(data), result.etag)
        return result

    def action_contracts(self) -> tuple[ActionContract, ...]:
        return tuple(self._action_contracts.values())

    def _action(self, contract_id: str, operation: str) -> tuple[ActionContract, str]:
        contract = self._action_contracts.get(contract_id)
        if contract is None or operation not in contract.operations:
            raise ActionClientError("action operation is not declared by discovery")
        path = contract.operations[operation]
        if not self._valid_action_path(operation, path):
            raise ActionClientError("action endpoint is not allowlisted")
        return contract, path

    @staticmethod
    def _materialize_action_path(operation: str, template: str, identifier: str) -> str:
        placeholder = "{preview_id}" if operation == "authorize" else "{receipt_id}"
        if operation not in {"authorize", "receipt", "verify", "cancel", "rollback"} or not _ACTION_REF.fullmatch(identifier):
            raise ActionClientError("action identifier is invalid")
        if operation == "authorize" and not re.fullmatch(r"apv-[a-z0-9][a-z0-9-]{7,95}", identifier):
            raise ActionClientError("preview identifier is invalid")
        if _ACTION_TEMPLATE.findall(template) != [placeholder]:
            raise ActionClientError("action endpoint has an unexpected placeholder")
        path = template.replace(placeholder, identifier, 1)
        if _ACTION_TEMPLATE.search(path) or not ControlPlaneClient._valid_action_path(operation, template):
            raise ActionClientError("action endpoint could not be materialized")
        return path

    @staticmethod
    def _check_hash(value: object) -> str:
        if not isinstance(value, str) or not _HASH.fullmatch(value):
            raise ActionClientError("action hash is invalid")
        return value

    @staticmethod
    def _validate_action_response(
        operation: str,
        data: dict,
        contract: ActionContract,
        *,
        identifier: str | None = None,
        preview: dict | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        _reject_unsafe_output(data)
        common = {
            "preview_id", "preview_hash", "status", "owner_service", "owner_receipt_ref",
            "policy_decision_ref", "accepted_at", "verification_state", "actor_ref", "purpose",
            "audit_ref", "external_effect_state", "receipt_id", "action_contract_id",
            "action_contract_version", "target", "parameters", "idempotency_key",
        }
        if operation == "preview":
            required = {
                "preview_id", "preview_hash", "schema_version", "status", "action_contract_id",
                "action_contract_version", "owner_service", "owner_operation", "target", "parameters",
                "expected_version", "policy_decision_ref", "expires_at",
            }
            allowed = required | {"source_recommendation_id", "before_summary", "proposed_effect", "blast_radius", "risk", "reversibility", "verification_plan", "rollback_plan", "required_scope", "required_approvals"}
        else:
            required = common
            allowed = common
        if set(data) - allowed or not required.issubset(data):
            raise ActionClientError("action response failed its strict typed schema")
        if operation == "preview":
            if data["action_contract_id"] != contract.contract_id or data["action_contract_version"] != contract.version:
                raise ActionClientError("action response identity does not match discovery")
            ControlPlaneClient._check_hash(data["preview_hash"])
            if not isinstance(data["parameters"], dict) or not set(data["parameters"]).issubset(_ACTION_SAFE_PARAMETERS):
                raise ActionClientError("action response parameters are not safe")
            if not isinstance(data["target"], dict) or not isinstance(data["expires_at"], str):
                raise ActionClientError("action preview types are invalid")
        else:
            if (
                not isinstance(data["receipt_id"], str)
                or not isinstance(data["preview_id"], str)
                or not _PREVIEW_ID.fullmatch(data["preview_id"])
                or not isinstance(data["status"], str)
                or data["status"] not in {"accepted", "duplicate", "rolled_back"}
                or not isinstance(data["verification_state"], str)
                or data["verification_state"] not in {"pending", "verified", "rolled_back"}
                or not isinstance(data["external_effect_state"], str)
                or data["external_effect_state"] not in {"none", "applied", "unknown"}
                or data["action_contract_id"] != contract.contract_id
                or data["action_contract_version"] != contract.version
                or not isinstance(data["target"], dict)
                or not isinstance(data["parameters"], dict)
                or not set(data["parameters"]).issubset(_ACTION_SAFE_PARAMETERS)
                or not isinstance(data["idempotency_key"], str)
            ):
                raise ActionClientError("action receipt types are invalid")
            if operation == "authorize" and data["status"] not in {"accepted", "duplicate"}:
                raise ActionClientError("authorization receipt status is invalid")
            ControlPlaneClient._check_hash(data["preview_hash"])
        if operation == "authorize" and identifier is not None and data.get("preview_id") != identifier:
            raise ActionClientError("action preview identity does not match the request")
        if operation != "authorize" and identifier is not None and data.get("receipt_id") != identifier:
            raise ActionClientError("action receipt identity does not match the request")
        if preview is not None:
            for field in ("preview_id", "preview_hash", "policy_decision_ref", "action_contract_version", "target", "parameters"):
                if data.get(field) != preview.get(field):
                    raise ActionClientError("action response is not bound to the exact preview")
        if idempotency_key is not None and data.get("idempotency_key") != idempotency_key:
            raise ActionClientError("action receipt is not bound to the idempotency key")

    def _remember_preview(self, preview: dict, contract: ActionContract) -> dict:
        self._validate_action_response("preview", preview, contract)
        if preview.get("action_contract_id") != contract.contract_id or preview.get("action_contract_version") != contract.version:
            raise ActionClientError("preview contract does not match discovery")
        preview_id = preview.get("preview_id")
        if not isinstance(preview_id, str) or not _PREVIEW_ID.fullmatch(preview_id):
            raise ActionClientError("preview identifier is invalid")
        if preview.get("status") != "ready" or _expired(preview.get("expires_at")):
            raise ActionClientError("preview is stale, expired, or not ready")
        self._previews[preview_id] = copy.deepcopy(preview)
        return preview

    async def preview_action(self, contract_id: str, request: Mapping[str, object]) -> ClientResponse:
        contract, path = self._action(contract_id, "preview")
        body = dict(request)
        required = {"recommendation_id", "parameter_proposal_ref"}
        if "action_contract_id" in body and body["action_contract_id"] != contract.contract_id:
            raise ActionClientError("action contract does not match discovery")
        if "action_contract_version" in body and body["action_contract_version"] != contract.version:
            raise ActionClientError("action contract version does not match discovery")
        body.update({"action_contract_id": contract.contract_id, "action_contract_version": contract.version})
        allowed = required | {"action_contract_id", "action_contract_version"} | _ACTION_SAFE_PARAMETERS
        if set(body) - allowed or not required.issubset(body) or any(not isinstance(value, str) or not value for value in body.values()):
            raise ActionClientError("action parameters are outside the declared contract")
        result = await self._request_action("preview", path, body, contract)
        self._remember_preview(result.data, contract)
        return result

    async def authorize_action(self, contract_id: str, preview_id: str, preview_hash: str, idempotency_key: str, approval_reason: str, *, target: Mapping[str, object] | None = None, parameters: Mapping[str, object] | None = None) -> ClientResponse:
        contract, path = self._action(contract_id, "authorize")
        preview = self._previews.get(preview_id)
        if preview is None or preview.get("preview_hash") != preview_hash:
            raise ActionClientError("preview is unknown or changed")
        if _expired(preview.get("expires_at")):
            raise ActionClientError("preview is stale, expired, or not ready")
        if preview.get("status") not in {"ready", "authorized"}:
            raise ActionClientError("preview is stale, expired, or replay-invalid")
        if target is not None and dict(target) != preview.get("target"):
            self._previews.pop(preview_id, None)
            raise ActionClientError("changed target invalidated the preview")
        if parameters is not None and dict(parameters) != preview.get("parameters"):
            self._previews.pop(preview_id, None)
            raise ActionClientError("changed parameters invalidated the preview")
        self._check_hash(preview_hash)
        if not isinstance(idempotency_key, str) or len(idempotency_key) < 16 or len(idempotency_key) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", idempotency_key):
            raise ActionClientError("idempotency key is invalid")
        body = {
            "preview_id": preview_id,
            "preview_hash": preview_hash,
            "idempotency_key": idempotency_key,
            "approval_reason": approval_reason,
            "action_contract_id": contract.contract_id,
            "action_contract_version": contract.version,
            "policy_decision_ref": preview["policy_decision_ref"],
            "target": copy.deepcopy(preview["target"]),
            "parameters": copy.deepcopy(preview["parameters"]),
        }
        result = await self._request_action("authorize", self._materialize_action_path("authorize", path, preview_id), body, contract, status_codes={200, 202}, identifier=preview_id, preview=preview, idempotency_key=idempotency_key)
        self._previews[preview_id]["status"] = "authorized"
        self._previews[preview_id]["idempotency_key"] = idempotency_key
        return result

    async def receipt(self, contract_id: str, receipt_id: str) -> ClientResponse:
        contract, path = self._action(contract_id, "receipt")
        return await self._request_action("receipt", self._materialize_action_path("receipt", path, receipt_id), None, contract, identifier=receipt_id)

    async def verify_action(self, contract_id: str, receipt_id: str) -> ClientResponse:
        contract, path = self._action(contract_id, "verify")
        return await self._request_action("verify", self._materialize_action_path("verify", path, receipt_id), None, contract, identifier=receipt_id)

    async def cancel_action(self, contract_id: str, receipt_id: str) -> ClientResponse:
        contract, path = self._action(contract_id, "cancel")
        return await self._request_action("cancel", self._materialize_action_path("cancel", path, receipt_id), {}, contract, status_codes={200, 202}, identifier=receipt_id)

    async def rollback_action(self, contract_id: str, receipt_id: str) -> ClientResponse:
        contract, path = self._action(contract_id, "rollback")
        return await self._request_action("rollback", self._materialize_action_path("rollback", path, receipt_id), {}, contract, status_codes={200, 202}, identifier=receipt_id)

    async def _request_action(self, operation: str, path: str, body: dict | None, contract: ActionContract, *, status_codes: set[int] | None = None, identifier: str | None = None, preview: dict | None = None, idempotency_key: str | None = None) -> ClientResponse:
        if operation not in _ACTION_OPERATIONS or identifier is not None and not _materialized_action_path(operation, identifier, path):
            raise ActionClientError("action endpoint is not allowlisted")
        capability = contract.capabilities.get(operation)
        if not isinstance(capability, str) or not _ACTION_CAPABILITY.fullmatch(capability):
            raise ActionClientError("action capability is missing or undeclared")
        if contract.operations.get(operation) != _ACTION_ENDPOINT_TEMPLATES[operation]:
            raise ActionClientError("action endpoint does not match discovery")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._bearer}", "X-SK-Capability-Name": capability}
        content = None if body is None else _canonical(body)
        if content is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = await self._http.request("POST" if operation in {"preview", "authorize", "cancel", "rollback"} else "GET", self.origin + path, content=content, headers=headers)
        except httpx.TimeoutException as exc:
            raise ActionClientError("action request timed out", code="ACTION_TIMEOUT") from exc
        allowed = status_codes or {200}
        if response.status_code not in allowed:
            try:
                error = response.json()
            except ValueError as exc:
                raise ActionClientError("action response is not JSON", status_code=response.status_code) from exc
            code = error.get("code") if isinstance(error, dict) else None
            raise ActionClientError(f"action request failed: {response.status_code} {code or 'UNKNOWN'}", status_code=response.status_code, code=code)
        if len(response.content) > MAX_RESPONSE_BYTES or not response.headers.get("content-type", "").startswith("application/json"):
            raise ActionClientError("action response exceeded its bound or is not JSON", status_code=response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise ActionClientError("action response is not JSON", status_code=response.status_code) from exc
        if not isinstance(data, dict):
            raise ActionClientError("action response is not an object", status_code=response.status_code)
        self._validate_action_response(operation, data, contract, identifier=identifier, preview=preview, idempotency_key=idempotency_key)
        if operation in {"receipt", "verify", "cancel", "rollback"}:
            expected = self._receipts.get(data["receipt_id"])
            if expected is None:
                raise ActionClientError("action receipt is unknown")
            for field in (
                "preview_id",
                "preview_hash",
                "action_contract_id",
                "action_contract_version",
                "target",
                "parameters",
                "idempotency_key",
            ):
                if data.get(field) != expected.get(field):
                    raise ActionClientError("action receipt binding changed")
        if operation == "preview":
            self._remember_preview(data, contract)
        elif data.get("receipt_id"):
            self._receipts[data["receipt_id"]] = copy.deepcopy(data)
        return ClientResponse(copy.deepcopy(data), response.headers.get("etag"))

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
    "ActionClientError",
    "ActionContract",
    "ClientResponse",
    "ContractValidators",
    "ControlPlaneClient",
    "ControlPlaneClientError",
]
