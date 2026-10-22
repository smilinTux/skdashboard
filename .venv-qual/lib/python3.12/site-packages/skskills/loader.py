"""SKSkills Loader — spin up each skill as a local MCP server.

Each installed skill gets its own MCP server running on a unix domain socket.
Knowledge packs become MCP resources, tool scripts become MCP tools,
and hooks register as event listeners on the agent runtime.

Architecture:
    SkillLoader reads skill.yaml -> resolves entrypoints -> starts MCP server
    Server binds to unix:///tmp/skskill-{name}.sock
    Tools are callable via MCP protocol
    Knowledge is served as MCP resources
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .models import (
    SkillManifest,
    parse_skill_yaml,
)

logger = logging.getLogger("skskills.loader")

SOCKET_DIR = Path("/tmp")


def socket_path_for(skill_name: str) -> str:
    """Compute the unix socket path for a skill's MCP server.

    Args:
        skill_name: The skill's name (kebab-case).

    Returns:
        str: The unix socket path.
    """
    return str(SOCKET_DIR / f"skskill-{skill_name}.sock")


def resolve_entrypoint(entrypoint: str, skill_dir: Path) -> Optional[Callable]:
    """Resolve a tool/hook entrypoint to a callable.

    Entrypoints can be:
        - Python dotpath with function: "module.submodule:function_name"
        - Script path: "scripts/deploy.sh" (returns a subprocess wrapper)
        - Python file path: "tools/my_tool.py:run"

    Args:
        entrypoint: The entrypoint string from skill.yaml.
        skill_dir: The installed skill's directory.

    Returns:
        Callable or None if resolution fails.
    """
    if ":" in entrypoint:
        module_path, func_name = entrypoint.rsplit(":", 1)

        # Reason: entrypoint can be a file path ("tools/greet.py:run")
        # or a dotpath ("tools.greet:run") — try file path first
        py_file = skill_dir / module_path
        if py_file.exists() and py_file.suffix == ".py":
            return _load_from_file(py_file, func_name)

        py_file = skill_dir / module_path.replace(".", "/")
        if not py_file.suffix:
            py_file = py_file.with_suffix(".py")
        if py_file.exists():
            return _load_from_file(py_file, func_name)

        try:
            mod = importlib.import_module(module_path)
            return getattr(mod, func_name, None)
        except (ImportError, AttributeError):
            logger.warning("Failed to resolve entrypoint: %s", entrypoint)
            return None

    script = skill_dir / entrypoint
    if script.exists() and os.access(script, os.X_OK):
        return _make_script_runner(script)

    return None


def _load_from_file(py_file: Path, func_name: str) -> Optional[Callable]:
    """Load a function from a Python file by path.

    Args:
        py_file: Path to the .py file.
        func_name: Function name to extract.

    Returns:
        Callable or None.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    sys.modules[py_file.stem] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, func_name, None)


def _make_script_runner(script: Path) -> Callable:
    """Create a callable that runs an external script.

    Args:
        script: Path to the executable script.

    Returns:
        Callable that runs the script and returns stdout.
    """

    async def run_script(**kwargs: Any) -> str:
        env = {**os.environ, **{k.upper(): str(v) for k, v in kwargs.items()}}
        proc = await asyncio.create_subprocess_exec(
            str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Script failed (rc={proc.returncode}): {stderr.decode()}")
        return stdout.decode()

    return run_script


class SkillServer:
    """An MCP server instance for a single skill.

    Wraps the skill's tools as MCP tools, knowledge as resources,
    and manages the server lifecycle on a unix socket.

    Args:
        manifest: The skill's manifest.
        skill_dir: Path to the installed skill directory.
    """

    def __init__(self, manifest: SkillManifest, skill_dir: Path) -> None:
        self.manifest = manifest
        self.skill_dir = skill_dir
        self.socket = socket_path_for(manifest.name)
        self._resolved_tools: dict[str, Callable] = {}
        self._resolved_hooks: dict[str, Callable] = {}

    def resolve_all(self) -> None:
        """Resolve all tool and hook entrypoints to callables."""
        for tool_def in self.manifest.tools:
            fn = resolve_entrypoint(tool_def.entrypoint, self.skill_dir)
            if fn:
                self._resolved_tools[tool_def.name] = fn
                logger.info("Resolved tool: %s.%s", self.manifest.name, tool_def.name)
            else:
                logger.warning(
                    "Could not resolve tool: %s.%s (%s)",
                    self.manifest.name,
                    tool_def.name,
                    tool_def.entrypoint,
                )

        for hook_def in self.manifest.hooks:
            fn = resolve_entrypoint(hook_def.entrypoint, self.skill_dir)
            if fn:
                self._resolved_hooks[hook_def.event.value] = fn
                logger.info("Resolved hook: %s -> %s", hook_def.event.value, hook_def.entrypoint)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return MCP-compatible tool schemas for all resolved tools.

        Returns:
            list[dict]: Tool schemas ready for MCP registration.
        """
        schemas: list[dict[str, Any]] = []
        for tool_def in self.manifest.tools:
            if tool_def.name in self._resolved_tools:
                schemas.append({
                    "name": f"{self.manifest.name}.{tool_def.name}",
                    "description": tool_def.description,
                    "inputSchema": tool_def.input_schema,
                })
        return schemas

    def get_resource_list(self) -> list[dict[str, str]]:
        """Return MCP-compatible resource list for knowledge packs.

        Returns:
            list[dict]: Resource definitions for MCP.
        """
        resources: list[dict[str, str]] = []
        for kp in self.manifest.knowledge:
            full_path = self.skill_dir / kp.path
            if full_path.exists():
                resources.append({
                    "uri": f"skill://{self.manifest.name}/{kp.path}",
                    "name": kp.path,
                    "description": kp.description or f"Knowledge from {self.manifest.name}",
                    "mimeType": kp.mime_type,
                })
        return resources

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Invoke a resolved tool by name.

        Args:
            tool_name: The local tool name (without skill prefix).
            arguments: Tool input arguments.

        Returns:
            Tool execution result.

        Raises:
            KeyError: If the tool isn't resolved.
            RuntimeError: If tool execution fails.
        """
        fn = self._resolved_tools.get(tool_name)
        if fn is None:
            raise KeyError(f"Tool not found: {self.manifest.name}.{tool_name}")

        if inspect.iscoroutinefunction(fn):
            return await fn(**arguments)
        return fn(**arguments)

    async def read_resource(self, uri: str) -> Optional[str]:
        """Read a knowledge pack resource by URI.

        Args:
            uri: Resource URI (e.g., skill://syncthing-setup/SKILL.md).

        Returns:
            str: Resource content, or None if not found.
        """
        prefix = f"skill://{self.manifest.name}/"
        if not uri.startswith(prefix):
            return None

        rel_path = uri[len(prefix):]
        full_path = self.skill_dir / rel_path
        if full_path.exists():
            return full_path.read_text()
        return None

    async def fire_hook(self, event: str, context: Optional[dict] = None) -> Any:
        """Fire a hook for a given event.

        Args:
            event: The event name (e.g., "on_boot").
            context: Optional context data for the hook.

        Returns:
            Hook execution result, or None if no hook bound.
        """
        fn = self._resolved_hooks.get(event)
        if fn is None:
            return None

        ctx = context or {}
        if inspect.iscoroutinefunction(fn):
            return await fn(**ctx)
        return fn(**ctx)


class SkillLoader:
    """Manages loading and lifecycle of skill MCP servers.

    Args:
        registry_root: Path to the skskills registry root.
    """

    def __init__(self, registry_root: Optional[Path] = None) -> None:
        import os as _os
        env = _os.environ.get("SKSKILLS_HOME")
        default = Path(env) if env else Path("~/.skskills")
        self.root = (registry_root or default).expanduser()
        self._servers: dict[str, SkillServer] = {}

    def load(self, skill_dir: Path) -> SkillServer:
        """Load a skill and prepare its MCP server.

        Args:
            skill_dir: Path to the installed skill directory.

        Returns:
            SkillServer: The loaded skill server (not yet started).
        """
        manifest = parse_skill_yaml(skill_dir / "skill.yaml")
        server = SkillServer(manifest, skill_dir)
        server.resolve_all()
        self._servers[manifest.name] = server
        logger.info("Loaded skill: %s (%d tools, %d hooks, %d knowledge)",
                     manifest.name,
                     len(server._resolved_tools),
                     len(server._resolved_hooks),
                     len(manifest.knowledge))
        return server

    def get_server(self, name: str) -> Optional[SkillServer]:
        """Get a loaded skill server by name.

        Args:
            name: Skill name.

        Returns:
            SkillServer or None.
        """
        return self._servers.get(name)

    def all_servers(self) -> list[SkillServer]:
        """Return all loaded skill servers.

        Returns:
            list[SkillServer]: All currently loaded servers.
        """
        return list(self._servers.values())

    def all_tools(self) -> list[dict[str, Any]]:
        """Collect tool schemas from all loaded skills.

        Returns:
            list[dict]: Aggregated MCP tool schemas.
        """
        tools: list[dict[str, Any]] = []
        for server in self._servers.values():
            tools.extend(server.get_tool_schemas())
        return tools

    def all_resources(self) -> list[dict[str, str]]:
        """Collect resource definitions from all loaded skills.

        Returns:
            list[dict]: Aggregated MCP resource definitions.
        """
        resources: list[dict[str, str]] = []
        for server in self._servers.values():
            resources.extend(server.get_resource_list())
        return resources

    async def call_tool(self, qualified_name: str, arguments: dict) -> Any:
        """Route a tool call to the correct skill server.

        Args:
            qualified_name: Fully-qualified tool name (skill.tool).
            arguments: Tool input arguments.

        Returns:
            Tool execution result.

        Raises:
            KeyError: If skill or tool not found.
        """
        if "." not in qualified_name:
            raise KeyError(f"Tool name must be qualified: skill_name.tool_name, got '{qualified_name}'")

        skill_name, tool_name = qualified_name.split(".", 1)
        server = self._servers.get(skill_name)
        if server is None:
            raise KeyError(f"Skill not loaded: {skill_name}")

        return await server.call_tool(tool_name, arguments)

    async def fire_event(self, event: str, context: Optional[dict] = None) -> dict[str, Any]:
        """Fire a lifecycle event across all loaded skills.

        Args:
            event: Event name (e.g., "on_boot").
            context: Optional context data.

        Returns:
            dict: Mapping of skill_name -> hook result.
        """
        results: dict[str, Any] = {}
        for name, server in self._servers.items():
            try:
                result = await server.fire_hook(event, context)
                if result is not None:
                    results[name] = result
            except Exception as exc:
                logger.error("Hook '%s' failed in skill '%s': %s", event, name, exc)
                results[name] = {"error": str(exc)}
        return results

    def unload(self, name: str) -> bool:
        """Unload a skill server.

        Args:
            name: Skill name to unload.

        Returns:
            bool: True if the skill was loaded and removed.
        """
        if name in self._servers:
            socket = Path(self._servers[name].socket)
            if socket.exists():
                socket.unlink(missing_ok=True)
            del self._servers[name]
            return True
        return False
