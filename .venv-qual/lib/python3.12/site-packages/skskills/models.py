"""SKSkills data models — skill.yaml schema as Pydantic models.

A skill has three primitive types:
  - Knowledge: context files (SKILL.md, references/) loaded as MCP resources
  - Tool: executable functions exposed as MCP tools
  - Hook: event-driven scripts triggered by agent lifecycle events
"""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class SkillType(str, enum.Enum):
    """The three primitive skill component types."""

    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    HOOK = "hook"


class SkillStatus(str, enum.Enum):
    """Installation/runtime status of a skill."""

    AVAILABLE = "available"
    INSTALLED = "installed"
    RUNNING = "running"
    ERROR = "error"
    DISABLED = "disabled"


class HookEvent(str, enum.Enum):
    """Agent lifecycle events that hooks can bind to."""

    ON_BOOT = "on_boot"
    ON_SHUTDOWN = "on_shutdown"
    ON_MESSAGE_RECEIVED = "on_message_received"
    ON_MESSAGE_SENT = "on_message_sent"
    ON_MEMORY_STORED = "on_memory_stored"
    ON_TASK_COMPLETED = "on_task_completed"
    ON_SKILL_INSTALLED = "on_skill_installed"
    ON_SYNC_PULL = "on_sync_pull"
    ON_SYNC_PUSH = "on_sync_push"
    CRON = "cron"


class KnowledgePack(BaseModel):
    """A context file that becomes an MCP resource.

    Knowledge packs are the SKILL.md files, reference docs,
    and any static context the skill provides to agents.
    """

    path: str = Field(description="Relative path within the skill directory")
    description: str = Field(default="", description="What this knowledge provides")
    mime_type: str = Field(default="text/markdown", description="Content MIME type")
    auto_load: bool = Field(default=False, description="Load into context on skill start")


class ToolDefinition(BaseModel):
    """An executable function exposed as an MCP tool.

    Tools can be Python functions, shell scripts, or HTTP endpoints.
    Each gets its own MCP tool registration.
    """

    name: str = Field(description="Tool name (becomes skill_name.tool_name in MCP)")
    description: str = Field(description="What this tool does")
    entrypoint: str = Field(description="Python dotpath or script path (e.g., 'tools.deploy:run')")
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
        description="JSON Schema for tool input",
    )
    timeout_s: int = Field(default=30, description="Execution timeout in seconds")
    requires_confirmation: bool = Field(
        default=False, description="Require user confirmation before execution"
    )


class HookDefinition(BaseModel):
    """An event-driven script triggered by agent lifecycle events.

    Hooks fire automatically when their bound event occurs.
    They're the reactive glue between skills and the agent runtime.
    """

    event: HookEvent = Field(description="The lifecycle event to bind to")
    entrypoint: str = Field(description="Python dotpath or script path to execute")
    description: str = Field(default="", description="What this hook does")
    cron_schedule: Optional[str] = Field(
        default=None, description="Cron expression (only for event=cron)"
    )
    async_: bool = Field(default=True, alias="async", description="Run asynchronously")


class SkillDependency(BaseModel):
    """A dependency on another skill or Python package."""

    name: str = Field(description="Package or skill name")
    version: str = Field(default="*", description="Version constraint (semver)")
    type: str = Field(default="skill", description="'skill' or 'python'")


class SkillAuthor(BaseModel):
    """Skill author with optional CapAuth identity."""

    name: str
    email: str = ""
    fingerprint: str = Field(default="", description="CapAuth PGP fingerprint for verification")


class SkillManifest(BaseModel):
    """The complete skill definition — parsed from skill.yaml.

    This is the soul of a skill. It declares what the skill provides
    (knowledge, tools, hooks), who made it, what it depends on,
    and how to verify its authenticity.
    """

    name: str = Field(description="Unique skill identifier (kebab-case)")
    version: str = Field(default="0.1.0", description="Semver version string")
    description: str = Field(default="", description="Human-readable description")
    author: SkillAuthor = Field(default_factory=lambda: SkillAuthor(name="unknown"))

    knowledge: list[KnowledgePack] = Field(
        default_factory=list, description="Knowledge packs (context files)"
    )
    tools: list[ToolDefinition] = Field(
        default_factory=list, description="MCP tool definitions"
    )
    hooks: list[HookDefinition] = Field(
        default_factory=list, description="Event-driven hooks"
    )

    dependencies: list[SkillDependency] = Field(
        default_factory=list, description="Required skills or Python packages"
    )
    python_requires: str = Field(default=">=3.10", description="Python version constraint")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")

    signature: str = Field(default="", description="CapAuth detached PGP signature of skill.yaml")
    signed_by: str = Field(default="", description="Fingerprint of the signer")

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Enforce kebab-case naming convention."""
        if not v or not all(c.isalnum() or c == "-" for c in v):
            raise ValueError(f"Skill name must be kebab-case: got '{v}'")
        return v.lower()

    @property
    def component_types(self) -> set[SkillType]:
        """Return which primitive types this skill provides."""
        types: set[SkillType] = set()
        if self.knowledge:
            types.add(SkillType.KNOWLEDGE)
        if self.tools:
            types.add(SkillType.TOOL)
        if self.hooks:
            types.add(SkillType.HOOK)
        return types

    @property
    def tool_names(self) -> list[str]:
        """Fully-qualified tool names for MCP registration."""
        return [f"{self.name}.{t.name}" for t in self.tools]

    def is_signed(self) -> bool:
        """Check if the skill has a CapAuth signature."""
        return bool(self.signature and self.signed_by)


class InstalledSkill(BaseModel):
    """Metadata for a skill installed in the local registry."""

    manifest: SkillManifest
    install_path: str = Field(description="Absolute path to the installed skill directory")
    installed_at: datetime = Field(default_factory=datetime.now)
    installed_by: str = Field(default="cli", description="How it was installed")
    agent: str = Field(default="global", description="Agent namespace ('global' or agent name)")
    status: SkillStatus = Field(default=SkillStatus.INSTALLED)
    socket_path: str = Field(default="", description="Unix socket path when running")
    pid: Optional[int] = Field(default=None, description="Process ID when running")


def parse_skill_yaml(path: Path) -> SkillManifest:
    """Parse a skill.yaml file into a SkillManifest.

    Args:
        path: Path to the skill.yaml file.

    Returns:
        SkillManifest: The parsed skill manifest.

    Raises:
        FileNotFoundError: If skill.yaml doesn't exist.
        ValueError: If the YAML is invalid or missing required fields.
    """
    if not path.exists():
        raise FileNotFoundError(f"skill.yaml not found: {path}")

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"skill.yaml must be a YAML mapping, got {type(raw).__name__}")

    return SkillManifest.model_validate(raw)


def generate_skill_yaml(manifest: SkillManifest) -> str:
    """Serialize a SkillManifest back to YAML.

    Args:
        manifest: The skill manifest to serialize.

    Returns:
        str: YAML string representation.
    """
    data = manifest.model_dump(exclude_none=True, exclude_defaults=False, by_alias=True)
    return yaml.dump(data, default_flow_style=False, sort_keys=False)
