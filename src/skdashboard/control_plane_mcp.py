"""MCP resources over the allowlisted read-only control-plane client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents

from .control_plane_client import (
    ClientResponse,
    ControlPlaneClient,
    ControlPlaneClientError,
    _reject_protected,
)

_FIXED = {
    "skdashboard://control-plane/health": ("Control-plane health", "health"),
    "skdashboard://control-plane/overview": ("Estate overview", "overview"),
    "skdashboard://control-plane/board": ("Board summary", "board"),
    "skdashboard://control-plane/fleet": ("Fleet summary", "fleet"),
    "skdashboard://control-plane/economy": ("Economy summary", "economy"),
}
_MAX_BEARER_BYTES = 64 * 1024


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


class ControlPlaneTools:
    """Explicit-capability command tools over the same canonical client."""

    def __init__(self, client: ControlPlaneClient):
        self.client = client

    async def preview(self, arguments: dict[str, object]) -> ClientResponse:
        allowed = {
            "recommendation_id",
            "action_contract_id",
            "parameter_proposal_ref",
            "capability",
        }
        if set(arguments) != allowed:
            raise ControlPlaneClientError("MCP tool arguments are outside the contract")
        return await self.client.preview_action(
            str(arguments.get("recommendation_id", "")),
            str(arguments.get("action_contract_id", "")),
            str(arguments.get("parameter_proposal_ref", "")),
            capability=str(arguments.get("capability", "")),
        )

    async def submit(self, arguments: dict[str, object]) -> ClientResponse:
        allowed = {
            "preview_id",
            "preview_hash",
            "idempotency_key",
            "approval_reason",
            "capability",
        }
        if set(arguments) != allowed:
            raise ControlPlaneClientError("MCP tool arguments are outside the contract")
        return await self.client.submit_action(
            str(arguments.get("preview_id", "")),
            str(arguments.get("preview_hash", "")),
            str(arguments.get("idempotency_key", "")),
            str(arguments.get("approval_reason", "")),
            capability=str(arguments.get("capability", "")),
        )


def create_mcp_server(client: ControlPlaneClient) -> Server:
    resources = ControlPlaneResources(client)
    tools = ControlPlaneTools(client)
    server = Server("skdashboard-read-only")

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

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="skdashboard_preview_action",
                description="Preview one allowlisted action with an explicit capability.",
                inputSchema={
                    "type": "object",
                    "required": [
                        "recommendation_id",
                        "action_contract_id",
                        "parameter_proposal_ref",
                        "capability",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "recommendation_id": {"type": "string"},
                        "action_contract_id": {"type": "string"},
                        "parameter_proposal_ref": {"type": "string"},
                        "capability": {"const": "skdashboard.actions.preview"},
                    },
                },
            ),
            types.Tool(
                name="skdashboard_submit_action",
                description="Submit one exact approved preview with an explicit capability.",
                inputSchema={
                    "type": "object",
                    "required": [
                        "preview_id",
                        "preview_hash",
                        "idempotency_key",
                        "approval_reason",
                        "capability",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "preview_id": {"type": "string"},
                        "preview_hash": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                        "approval_reason": {"type": "string"},
                        "capability": {"const": "skdashboard.actions.authorize"},
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, object]) -> list[types.TextContent]:
        if name == "skdashboard_preview_action":
            response = await tools.preview(arguments)
        elif name == "skdashboard_submit_action":
            response = await tools.submit(arguments)
        else:
            raise ControlPlaneClientError("MCP tool is not allowlisted")
        _reject_protected(response.data)
        return [types.TextContent(type="text", text=json.dumps(response.data, sort_keys=True))]

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


__all__ = ["ControlPlaneResources", "ControlPlaneTools", "create_mcp_server", "main"]
