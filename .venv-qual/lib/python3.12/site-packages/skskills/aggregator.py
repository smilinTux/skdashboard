"""SKSkills Aggregator — central MCP server exposing all skill tools.

The aggregator connects to all running skill servers and presents
their tools and resources through a single unified MCP endpoint.
This is the bridge between skcapstone MCP and individual skills.

Architecture:
    Agent MCP Client
         |
    SKCapstone MCP Server
         |
    SkillAggregator (this)
         |
    +----+----+----+
    |    |    |    |
   Skill Skill Skill ...
   (unix sockets)

Discovery paths (in priority order):
    1. ~/.skskills/agents/<agent>/   (per-agent namespace)
    2. ~/.skskills/installed/         (global registry)
    3. ~/.skcapstone/skills/          (skcapstone built-in skills)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from .loader import SkillLoader
from .models import SkillStatus
from .registry import SkillRegistry

logger = logging.getLogger("skskills.aggregator")


# ---------------------------------------------------------------------------
# Health types
# ---------------------------------------------------------------------------


@dataclass
class SkillHealth:
    """Per-skill health record populated during load and health checks.

    Attributes:
        skill_name: The skill identifier.
        status: Current health state ('ok', 'degraded', 'error').
        tools_resolved: Number of tools successfully resolved.
        tools_failed: Tool names that failed to resolve (entrypoint missing).
        load_error: Exception message when the skill itself failed to load.
        last_check: Timestamp of the most recent health evaluation.
        source: Where the skill was discovered ('registry', 'skcapstone').
    """

    skill_name: str
    status: str = "unknown"       # "ok" | "degraded" | "error" | "unknown"
    tools_resolved: int = 0
    tools_failed: list[str] = field(default_factory=list)
    load_error: Optional[str] = None
    last_check: Optional[datetime] = None
    source: str = "registry"      # "registry" | "skcapstone"


class SkillAggregator:
    """Aggregates all skill MCP servers into a single endpoint.

    Discovers installed skills, loads them via SkillLoader,
    and exposes their tools/resources through the MCP protocol.

    Discovery happens from three sources (agent skills override global):
    - Per-agent registry: ``~/.skskills/agents/<agent>/``
    - Global registry:    ``~/.skskills/installed/``
    - skcapstone built-ins: ``~/.skcapstone/skills/``

    Args:
        agent: Agent name for per-agent skill resolution.
        registry_root: Path to ~/.skskills.
        skcapstone_home: Path to ~/.skcapstone (for built-in skills).
    """

    def __init__(
        self,
        agent: str = "global",
        registry_root: Optional[Path] = None,
        skcapstone_home: Optional[Path] = None,
    ) -> None:
        self.agent = agent
        self.registry = SkillRegistry(registry_root)
        self.loader = SkillLoader(registry_root)
        self._skcapstone_home: Path = (
            skcapstone_home or Path(os.environ.get("SKCAPSTONE_HOME", "~/.skcapstone"))
        ).expanduser()
        # Health state: skill_name -> SkillHealth
        self._health: dict[str, SkillHealth] = {}
        # Collision map: base_tool_name -> [qualified "skill.tool" names]
        self._collisions: dict[str, list[str]] = {}
        self._mcp_server = Server("skskills-aggregator")
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Register MCP protocol handlers."""

        @self._mcp_server.list_tools()
        async def list_tools() -> list[Tool]:
            builtin = [
                Tool(
                    name="skskills.list",
                    description="List all installed skills and their status",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "description": "Filter by agent (default: all)",
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="skskills.skills",
                    description="List all loaded (active) skills with their tool namespaces",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                ),
                Tool(
                    name="skskills.info",
                    description="Get detailed information about a specific skill",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Skill name to inspect",
                            },
                        },
                        "required": ["name"],
                    },
                ),
                Tool(
                    name="skskills.run_tool",
                    description="Run a specific skill tool by its qualified name (skill_name.tool_name)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "Fully-qualified tool name, e.g. 'syncthing-setup.check_status'",
                            },
                            "args": {
                                "type": "object",
                                "description": "Arguments to pass to the tool",
                            },
                        },
                        "required": ["tool"],
                    },
                ),
                Tool(
                    name="skskills.health",
                    description=(
                        "Health status for all loaded skills. "
                        "Reports per-skill status (ok/degraded/error), "
                        "unresolved tools, and load errors."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "skill": {
                                "type": "string",
                                "description": "Skill name to check (omit for all)",
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="skskills.collisions",
                    description=(
                        "List tool namespace collisions — cases where two or more skills "
                        "define tools sharing the same base name. The qualified name "
                        "(skill.tool) always remains unique; this reports potential "
                        "confusion points."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                ),
            ]

            # Only expose tools from enabled skills
            skill_tools = []
            for schema in self.loader.all_tools():
                skill_name = schema["name"].split(".")[0]
                if self._is_skill_enabled(skill_name):
                    skill_tools.append(Tool(
                        name=schema["name"],
                        description=schema["description"],
                        inputSchema=schema["inputSchema"],
                    ))

            return builtin + skill_tools

        @self._mcp_server.list_resources()
        async def list_resources() -> list[Resource]:
            resources = []
            for res in self.loader.all_resources():
                resources.append(Resource(
                    uri=res["uri"],
                    name=res["name"],
                    description=res.get("description", ""),
                    mimeType=res.get("mimeType", "text/plain"),
                ))
            return resources

        @self._mcp_server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            return await self._handle_tool_call(name, arguments)

        @self._mcp_server.read_resource()
        async def read_resource(uri: str) -> str:
            return await self._handle_read_resource(uri)

    def _is_skill_enabled(self, skill_name: str) -> bool:
        """Check whether a skill is enabled in the registry.

        Args:
            skill_name: Skill name to check.

        Returns:
            bool: True if enabled (INSTALLED or RUNNING), False if DISABLED.
        """
        skill = self.registry.get(skill_name, self.agent)
        if skill is None:
            # Also check global
            skill = self.registry.get(skill_name, "global")
        if skill is None:
            return True  # loaded but not in registry — allow by default
        return skill.status != SkillStatus.DISABLED

    async def _handle_tool_call(self, name: str, arguments: dict) -> list[TextContent]:
        """Route a tool call to the appropriate handler.

        Args:
            name: Fully-qualified tool name.
            arguments: Tool arguments.

        Returns:
            list[TextContent]: MCP response.
        """
        if name == "skskills.list":
            return self._handle_list(arguments)

        if name == "skskills.skills":
            return self._handle_skills_endpoint()

        if name == "skskills.info":
            return self._handle_info(arguments)

        if name == "skskills.run_tool":
            tool = arguments.get("tool", "")
            args = arguments.get("args", {})
            return await self._handle_tool_call(tool, args)

        if name == "skskills.health":
            return self._handle_health(arguments)

        if name == "skskills.collisions":
            return self._handle_collisions()

        try:
            result = await self.loader.call_tool(name, arguments)
            if isinstance(result, str):
                return [TextContent(type="text", text=result)]
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        except KeyError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]
        except Exception as exc:
            logger.exception("Tool '%s' failed", name)
            return [TextContent(type="text", text=json.dumps({"error": f"{name} failed: {exc}"}))]

    def _handle_skills_endpoint(self) -> list[TextContent]:
        """Return metadata for all currently loaded (active) skills.

        Returns:
            list[TextContent]: JSON list of loaded skill summaries.
        """
        data = []
        for server in self.loader.all_servers():
            m = server.manifest
            data.append({
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "enabled": self._is_skill_enabled(m.name),
                "tools": [f"{m.name}.{t.name}" for t in m.tools],
                "knowledge_packs": len(m.knowledge),
                "hooks": [h.event.value for h in m.hooks],
                "tags": m.tags,
                "signed": m.is_signed(),
            })
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    def _handle_list(self, arguments: dict) -> list[TextContent]:
        """List installed skills."""
        agent = arguments.get("agent")
        skills = self.registry.list_skills(agent)
        data = [
            {
                "name": s.manifest.name,
                "version": s.manifest.version,
                "agent": s.agent,
                "types": [t.value for t in s.manifest.component_types],
                "tools": s.manifest.tool_names,
                "status": s.status.value,
                "signed": s.manifest.is_signed(),
            }
            for s in skills
        ]
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    def _handle_info(self, arguments: dict) -> list[TextContent]:
        """Get info about a specific skill."""
        name = arguments.get("name", "")
        if not name:
            return [TextContent(type="text", text=json.dumps({"error": "name is required"}))]

        server = self.loader.get_server(name)
        if server is None:
            skill = self.registry.get(name, self.agent)
            if skill is None:
                return [TextContent(type="text", text=json.dumps({"error": f"Skill not found: {name}"}))]
            m = skill.manifest
        else:
            m = server.manifest

        data = {
            "name": m.name,
            "version": m.version,
            "description": m.description,
            "author": m.author.model_dump(),
            "types": [t.value for t in m.component_types],
            "tools": [
                {"name": t.name, "description": t.description}
                for t in m.tools
            ],
            "knowledge": [
                {"path": k.path, "description": k.description}
                for k in m.knowledge
            ],
            "hooks": [
                {"event": h.event.value, "description": h.description}
                for h in m.hooks
            ],
            "dependencies": [
                {"name": d.name, "version": d.version, "type": d.type}
                for d in m.dependencies
            ],
            "tags": m.tags,
            "signed": m.is_signed(),
            "signed_by": m.signed_by,
        }
        return [TextContent(type="text", text=json.dumps(data, indent=2))]

    def _handle_health(self, arguments: dict) -> list[TextContent]:
        """Return per-skill health status.

        Args:
            arguments: Optional ``skill`` key to filter to a single skill.

        Returns:
            list[TextContent]: JSON health report.
        """
        skill_filter = arguments.get("skill")

        if skill_filter:
            health = self._health.get(skill_filter)
            if health is None:
                return [TextContent(type="text", text=json.dumps({
                    "error": f"Skill not loaded: {skill_filter}",
                }))]
            return [TextContent(type="text", text=json.dumps(
                self._health_to_dict(health), indent=2
            ))]

        report = {
            "summary": self._health_summary(),
            "skills": {
                name: self._health_to_dict(h)
                for name, h in sorted(self._health.items())
            },
        }
        return [TextContent(type="text", text=json.dumps(report, indent=2))]

    def _handle_collisions(self) -> list[TextContent]:
        """Return detected tool namespace collisions.

        Returns:
            list[TextContent]: JSON collision report.
        """
        if not self._collisions:
            return [TextContent(type="text", text=json.dumps({
                "collisions": 0,
                "details": {},
            }, indent=2))]

        return [TextContent(type="text", text=json.dumps({
            "collisions": len(self._collisions),
            "note": (
                "Qualified names (skill.tool) remain unique. "
                "These are base-name overlaps that may cause confusion."
            ),
            "details": self._collisions,
        }, indent=2))]

    @staticmethod
    def _health_to_dict(h: "SkillHealth") -> dict:
        return {
            "skill": h.skill_name,
            "status": h.status,
            "tools_resolved": h.tools_resolved,
            "tools_failed": h.tools_failed,
            "load_error": h.load_error,
            "source": h.source,
            "last_check": h.last_check.isoformat() if h.last_check else None,
        }

    def _health_summary(self) -> dict:
        counts: dict[str, int] = {"ok": 0, "degraded": 0, "error": 0, "unknown": 0}
        for h in self._health.values():
            counts[h.status] = counts.get(h.status, 0) + 1
        return {
            "total": len(self._health),
            **counts,
        }

    async def _handle_read_resource(self, uri: str) -> str:
        """Route resource read to the correct skill server.

        Args:
            uri: Resource URI (e.g., skill://syncthing-setup/SKILL.md).

        Returns:
            str: Resource content.

        Raises:
            ValueError: If resource not found.
        """
        for server in self.loader.all_servers():
            content = await server.read_resource(uri)
            if content is not None:
                return content
        raise ValueError(f"Resource not found: {uri}")

    def load_all_skills(self) -> int:
        """Discover and load all enabled installed skills for the current agent.

        Discovery order (highest priority first):
        1. Per-agent registry (``~/.skskills/agents/<agent>/``)
        2. Global registry (``~/.skskills/installed/``)
        3. skcapstone built-in skills (``~/.skcapstone/skills/``)

        Agent-specific skills override global and skcapstone skills sharing
        the same name. Disabled registry skills are skipped. After loading,
        health records are built and tool namespace collisions are detected.

        Returns:
            int: Number of skills successfully loaded.
        """
        self._health.clear()
        self._collisions.clear()

        # ── 1 & 2: registry skills (agent > global) ────────────────────────
        skills = self.registry.list_skills(self.agent)
        if self.agent != "global":
            skills.extend(self.registry.list_skills("global"))

        loaded = 0
        seen: set[str] = set()

        for skill in skills:
            name = skill.manifest.name
            if name in seen:
                continue
            seen.add(name)

            if skill.status == SkillStatus.DISABLED:
                logger.info("Skipping disabled skill: %s", name)
                continue

            health = SkillHealth(skill_name=name, source="registry", last_check=datetime.now())
            try:
                server = self.loader.load(Path(skill.install_path))
                health.tools_resolved = len(server._resolved_tools)
                health.tools_failed = [
                    t.name for t in server.manifest.tools
                    if t.name not in server._resolved_tools
                ]
                health.status = "ok" if not health.tools_failed else "degraded"
                loaded += 1
            except Exception as exc:
                logger.error("Failed to load skill '%s': %s", name, exc)
                health.status = "error"
                health.load_error = str(exc)

            self._health[name] = health

        # ── 3: skcapstone built-in skills ──────────────────────────────────
        for skill_dir in self._scan_skcapstone_skills():
            name = skill_dir.name
            if name in seen:
                # Registry/agent version already loaded — skip
                continue
            seen.add(name)

            health = SkillHealth(skill_name=name, source="skcapstone", last_check=datetime.now())
            try:
                server = self.loader.load(skill_dir)
                health.tools_resolved = len(server._resolved_tools)
                health.tools_failed = [
                    t.name for t in server.manifest.tools
                    if t.name not in server._resolved_tools
                ]
                health.status = "ok" if not health.tools_failed else "degraded"
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load skcapstone skill '%s': %s", name, exc)
                health.status = "error"
                health.load_error = str(exc)

            self._health[name] = health

        self._detect_collisions()
        logger.info(
            "Loaded %d skills for agent '%s' (%d collisions detected)",
            loaded, self.agent, len(self._collisions),
        )
        return loaded

    def _scan_skcapstone_skills(self) -> list[Path]:
        """Discover skill directories under ``~/.skcapstone/skills/``.

        Scans two locations in priority order:
        1. Per-agent skills at ``~/.skcapstone/skills/agents/<agent>/``
           (synced via Syncthing; agent-specific overrides global)
        2. Global skcapstone skills at ``~/.skcapstone/skills/``

        Each sub-directory that contains a ``skill.yaml`` is treated as an
        installable skill. This lets skcapstone ship first-party skills
        without requiring a separate registry install step.

        Returns:
            list[Path]: Skill directories sorted by name (agent overrides global).
        """
        skills_dir = self._skcapstone_home / "skills"
        if not skills_dir.is_dir():
            return []

        seen: set[str] = set()
        found: list[Path] = []

        # 1. Per-agent skcapstone skills (highest priority)
        if self.agent != "global":
            agent_skills_dir = skills_dir / "agents" / self.agent
            if agent_skills_dir.is_dir():
                for entry in sorted(agent_skills_dir.iterdir()):
                    if entry.is_dir() and (entry / "skill.yaml").exists():
                        seen.add(entry.name)
                        found.append(entry)

        # 2. Global skcapstone skills (skip if agent version already found)
        for entry in sorted(skills_dir.iterdir()):
            if entry.name == "agents":
                continue  # reserved for per-agent namespaces
            if entry.is_dir() and (entry / "skill.yaml").exists():
                if entry.name not in seen:
                    seen.add(entry.name)
                    found.append(entry)

        return found

    def _detect_collisions(self) -> None:
        """Populate ``_collisions`` with any shared base tool names.

        Iterates all loaded skills and groups their fully-qualified tool names
        by base tool name. Any base name claimed by more than one skill is
        recorded as a collision. Qualified names remain unique so proxying
        is always unambiguous; collisions are informational only.
        """
        base_to_qualified: dict[str, list[str]] = {}
        for server in self.loader.all_servers():
            for tool_def in server.manifest.tools:
                if tool_def.name not in server._resolved_tools:
                    continue  # unresolved tools don't collide
                qualified = f"{server.manifest.name}.{tool_def.name}"
                base_to_qualified.setdefault(tool_def.name, []).append(qualified)

        self._collisions = {
            base: names
            for base, names in base_to_qualified.items()
            if len(names) > 1
        }
        if self._collisions:
            for base, names in self._collisions.items():
                logger.warning(
                    "Tool name collision on '%s': %s",
                    base, ", ".join(names),
                )

    def get_loaded_skills(self) -> list[dict]:
        """Return a summary of all currently loaded skills.

        Returns:
            list[dict]: Skill summaries with name, version, tools, health, enabled status.
        """
        result = []
        for server in self.loader.all_servers():
            m = server.manifest
            health = self._health.get(m.name)
            result.append({
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "enabled": self._is_skill_enabled(m.name),
                "health": health.status if health else "unknown",
                "source": health.source if health else "unknown",
                "tools": [f"{m.name}.{t.name}" for t in m.tools],
                "knowledge_packs": len(m.knowledge),
                "hooks": [h.event.value for h in m.hooks],
                "tags": m.tags,
            })
        return result

    async def run_stdio(self) -> None:
        """Run the aggregator as an MCP server on stdio."""
        async with stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )


def main() -> None:
    """Entry point for the SKSkills aggregator MCP server (``skskills-aggregator``).

    Parses ``--agent`` and ``--log-level`` from argv, then starts the
    stdio MCP server serving all discovered skill tools.

    Usage::

        skskills-aggregator
        skskills-aggregator --agent jarvis
        skskills-aggregator --agent opus --log-level DEBUG
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="skskills-aggregator",
        description="SKSkills aggregator MCP server — proxies all installed skill tools",
    )
    parser.add_argument(
        "--agent",
        default="global",
        help="Agent namespace to load skills for (default: global)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(name)s: %(message)s")

    agg = SkillAggregator(agent=args.agent)
    count = agg.load_all_skills()
    health_summary = agg._health_summary()
    logger.warning(
        "SKSkills aggregator started: %d skills loaded for '%s' "
        "(ok=%d degraded=%d error=%d collisions=%d)",
        count,
        args.agent,
        health_summary.get("ok", 0),
        health_summary.get("degraded", 0),
        health_summary.get("error", 0),
        len(agg._collisions),
    )
    asyncio.run(agg.run_stdio())
