"""MCP resources over the allowlisted read-only control-plane client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents

from .control_plane_client import ClientResponse, ControlPlaneClient, ControlPlaneClientError

_FIXED = {
    "skdashboard://control-plane/health": ("Control-plane health", "health"),
    "skdashboard://control-plane/overview": ("Estate overview", "overview"),
    "skdashboard://control-plane/board": ("Board summary", "board"),
    "skdashboard://control-plane/fleet": ("Fleet summary", "fleet"),
    "skdashboard://control-plane/economy": ("Economy summary", "economy"),
}
_PROTECTED_KEYS = frozenset(
    {
        "tenant_id",
        "matter_id",
        "raw_content",
        "content_bytes",
        "capability_token",
        "bearer_token",
        "secret",
        "credential",
    }
)
_PROTECTED_KEY_PATTERN = re.compile(
    r"(?:bearer|token|credential|secret|password|tenant|matter|raw|content|endpoint|arbitrary)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
_MAX_BEARER_BYTES = 64 * 1024


def _reject_protected(value: object) -> None:
    if isinstance(value, dict):
        if _PROTECTED_KEYS.intersection(value) or any(_PROTECTED_KEY_PATTERN.search(str(key)) for key in value):
            raise ControlPlaneClientError("MCP resource contains protected fields")
        for child in value.values():
            if _BEARER_VALUE.search(str(child)):
                raise ControlPlaneClientError("MCP resource contains credential-like output")
            _reject_protected(child)
    elif isinstance(value, list):
        for child in value:
            _reject_protected(child)


def _read_bearer(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("bearer file is unavailable or unsafe") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("bearer file must be one regular mode 0600 file")
        content = b""
        while len(content) <= _MAX_BEARER_BYTES:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            content += chunk
        if len(content) > _MAX_BEARER_BYTES:
            raise ValueError("bearer file exceeds its read bound")
    finally:
        os.close(descriptor)
    try:
        bearer = content.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError("bearer file is not UTF-8") from error
    if not bearer or "\x00" in bearer or "\n" in bearer or "\r" in bearer:
        raise ValueError("bearer file content is invalid")
    return bearer


class ControlPlaneResources:
    """Expose fixed projections and hash-addressed reports, never tools."""

    def __init__(self, client: ControlPlaneClient):
        self.client = client

    def list(self) -> list[types.Resource]:
        return [
            types.Resource(
                name=name,
                uri=uri,
                description="Authorized, schema-validated read-only projection",
                mimeType="application/json",
            )
            for uri, (name, _operation) in _FIXED.items()
        ]

    def templates(self) -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(
                name="Immutable report snapshot",
                uriTemplate="skdashboard://control-plane/reports/{snapshot_id}",
                description="Authorized exact immutable report snapshot",
                mimeType="application/json",
            )
        ]

    async def read(self, uri: str) -> ClientResponse:
        if uri in _FIXED:
            response = await getattr(self.client, _FIXED[uri][1])()
            _reject_protected(response.data)
            return response
        parsed = urlsplit(uri)
        prefix = "/reports/"
        if (
            parsed.scheme == "skdashboard"
            and parsed.netloc == "control-plane"
            and parsed.path.startswith(prefix)
            and not parsed.query
            and not parsed.fragment
        ):
            response = await self.client.report(parsed.path[len(prefix) :])
            _reject_protected(response.data)
            return response
        raise ControlPlaneClientError("MCP resource URI is not allowlisted")


def create_mcp_server(client: ControlPlaneClient) -> Server:
    resources = ControlPlaneResources(client)
    server = Server("skdashboard-read-only")

    def tool_name(contract_id: str, operation: str) -> str:
        return f"action_{contract_id.replace('.', '_').replace(':', '_')}_{operation}"

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools = []
        for contract in client.action_contracts():
            for operation in contract.operations:
                if operation == "preview":
                    properties = {
                        field: {"type": "string", "minLength": 1, "maxLength": 512}
                        for field in contract.parameter_fields
                    }
                    required = ["recommendation_id", "parameter_proposal_ref"]
                elif operation == "authorize":
                    properties = {
                        "preview_id": {"type": "string", "pattern": "^apv-[a-z0-9][a-z0-9-]{7,95}$"},
                        "preview_hash": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
                        "idempotency_key": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"},
                        "approval_reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                    }
                    required = list(properties)
                else:
                    properties = {"receipt_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"}}
                    required = ["receipt_id"]
                tools.append(types.Tool(
                    name=tool_name(contract.contract_id, operation),
                    description=f"{operation.title()} one discovered {contract.contract_id} action using capability name {contract.capabilities[operation]}; never accepts bearer material.",
                    inputSchema={"type": "object", "additionalProperties": False, "properties": properties, "required": required},
                ))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        for contract in client.action_contracts():
            for operation in contract.operations:
                if name != tool_name(contract.contract_id, operation):
                    continue
                if operation == "preview":
                    response = await client.preview_action(contract.contract_id, arguments)
                elif operation == "authorize":
                    response = await client.authorize_action(contract.contract_id, **arguments)
                elif operation == "receipt":
                    response = await client.receipt(contract.contract_id, arguments["receipt_id"])
                elif operation == "verify":
                    response = await client.verify_action(contract.contract_id, arguments["receipt_id"])
                elif operation == "cancel":
                    response = await client.cancel_action(contract.contract_id, arguments["receipt_id"])
                else:
                    response = await client.rollback_action(contract.contract_id, arguments["receipt_id"])
                _reject_protected(response.data)
                return response.data
        raise ControlPlaneClientError("MCP action tool is not allowlisted")

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return resources.list()

    @server.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return resources.templates()

    @server.read_resource()
    async def read_resource(uri):
        response = await resources.read(str(uri))
        return [
            ReadResourceContents(
                content=json.dumps(response.data, sort_keys=True, separators=(",", ":")),
                mime_type="application/json",
                meta={"etag": response.etag} if response.etag else None,
            )
        ]

    return server


async def _run(discovery_url: str, bearer_file: Path) -> None:
    from mcp.server.stdio import stdio_server

    bearer = _read_bearer(bearer_file)
    client = await ControlPlaneClient.discover(discovery_url, bearer)
    server = create_mcp_server(client)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve read-only SKDashboard MCP resources")
    parser.add_argument("--discovery-url", required=True)
    parser.add_argument("--bearer-file", required=True, type=Path)
    args = parser.parse_args(argv)
    asyncio.run(_run(args.discovery_url, args.bearer_file))


__all__ = ["ControlPlaneResources", "create_mcp_server", "main"]
