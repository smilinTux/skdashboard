"""SKSkills Registry — local-first skill installation and management.

Directory layout:
    ~/.skskills/
        installed/              # Global skill installs
            syncthing-setup/
                skill.yaml
                knowledge/
                tools/
                hooks/
        agents/                 # Per-agent namespaces
            jarvis/
                syncthing-setup -> ../../installed/syncthing-setup  (symlink)
                custom-skill/
                    skill.yaml
                    ...
            lumina/
                ...
        registry.json           # Index of all installed skills
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import InstalledSkill, SkillStatus, parse_skill_yaml


def _default_registry_root() -> Path:
    """Resolve the default registry root, respecting SKSKILLS_HOME env var.

    Returns:
        Path: The registry root directory.
    """
    env = os.environ.get("SKSKILLS_HOME")
    if env:
        return Path(env)
    return Path("~/.skskills").expanduser()


class SkillRegistry:
    """Manages skill installation, discovery, and per-agent isolation.

    Args:
        root: Base directory for skills (default: SKSKILLS_HOME or ~/.skskills).
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or _default_registry_root()).expanduser()
        self.installed_dir = self.root / "installed"
        self.agents_dir = self.root / "agents"
        self.index_path = self.root / "registry.json"

    def ensure_dirs(self) -> None:
        """Create the directory structure if it doesn't exist."""
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)

    def install(
        self,
        source: Path,
        agent: str = "global",
        force: bool = False,
    ) -> InstalledSkill:
        """Install a skill from a local directory.

        Args:
            source: Path to the skill directory containing skill.yaml.
            agent: Agent namespace ('global' or specific agent name).
            force: Overwrite existing installation.

        Returns:
            InstalledSkill: Metadata for the installed skill.

        Raises:
            FileNotFoundError: If source or skill.yaml doesn't exist.
            ValueError: If skill is already installed and force=False.
        """
        self.ensure_dirs()
        skill_yaml = source / "skill.yaml"
        manifest = parse_skill_yaml(skill_yaml)

        target = self._skill_dir(manifest.name, agent)
        if target.exists() and not force:
            raise ValueError(
                f"Skill '{manifest.name}' already installed for agent '{agent}'. "
                f"Use force=True to overwrite."
            )

        if target.exists():
            shutil.rmtree(target)

        shutil.copytree(source, target)

        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(target),
            installed_at=datetime.now(),
            installed_by="registry",
            agent=agent,
            status=SkillStatus.INSTALLED,
        )

        self._update_index(installed)
        return installed

    def uninstall(self, name: str, agent: str = "global") -> bool:
        """Remove an installed skill.

        Args:
            name: Skill name to uninstall.
            agent: Agent namespace.

        Returns:
            bool: True if the skill was found and removed.
        """
        target = self._skill_dir(name, agent)
        if not target.exists():
            return False

        shutil.rmtree(target)
        self._remove_from_index(name, agent)
        return True

    def get(self, name: str, agent: str = "global") -> Optional[InstalledSkill]:
        """Look up an installed skill by name and agent.

        Args:
            name: Skill name.
            agent: Agent namespace.

        Returns:
            InstalledSkill or None if not found.
        """
        target = self._skill_dir(name, agent)
        skill_yaml = target / "skill.yaml"
        if not skill_yaml.exists():
            return None

        manifest = parse_skill_yaml(skill_yaml)
        index = self._load_index()
        key = f"{agent}/{name}"
        meta = index.get(key, {})

        return InstalledSkill(
            manifest=manifest,
            install_path=str(target),
            installed_at=datetime.fromisoformat(meta.get("installed_at", datetime.now().isoformat())),
            installed_by=meta.get("installed_by", "unknown"),
            agent=agent,
            status=SkillStatus(meta.get("status", "installed")),
            socket_path=meta.get("socket_path", ""),
            pid=meta.get("pid"),
        )

    def list_skills(self, agent: Optional[str] = None) -> list[InstalledSkill]:
        """List all installed skills, optionally filtered by agent.

        Args:
            agent: If provided, only list skills for this agent.

        Returns:
            list[InstalledSkill]: All matching installed skills.
        """
        results: list[InstalledSkill] = []

        if agent is None or agent == "global":
            results.extend(self._scan_dir(self.installed_dir, "global"))

        if agent is None:
            if self.agents_dir.exists():
                for agent_dir in sorted(self.agents_dir.iterdir()):
                    if agent_dir.is_dir():
                        results.extend(self._scan_dir(agent_dir, agent_dir.name))
        elif agent != "global":
            agent_path = self.agents_dir / agent
            if agent_path.exists():
                results.extend(self._scan_dir(agent_path, agent))

        return results

    def link_to_agent(self, name: str, agent: str) -> Path:
        """Symlink a global skill into an agent's namespace.

        Args:
            name: Skill name (must be installed globally).
            agent: Agent to link the skill to.

        Returns:
            Path: The symlink path.

        Raises:
            FileNotFoundError: If the global skill doesn't exist.
        """
        global_dir = self.installed_dir / name
        if not global_dir.exists():
            raise FileNotFoundError(f"Global skill '{name}' not found")

        agent_dir = self.agents_dir / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        link_path = agent_dir / name

        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()

        link_path.symlink_to(global_dir)
        return link_path

    def agent_skills(self, agent: str) -> list[str]:
        """List skill names available to a specific agent.

        Args:
            agent: Agent name.

        Returns:
            list[str]: Skill names (from agent namespace + global).
        """
        names: set[str] = set()

        for skill in self._scan_dir(self.installed_dir, "global"):
            names.add(skill.manifest.name)

        agent_path = self.agents_dir / agent
        if agent_path.exists():
            for skill in self._scan_dir(agent_path, agent):
                names.add(skill.manifest.name)

        return sorted(names)

    def _skill_dir(self, name: str, agent: str) -> Path:
        """Resolve the directory path for a skill."""
        if agent == "global":
            return self.installed_dir / name
        return self.agents_dir / agent / name

    def _scan_dir(self, directory: Path, agent: str) -> list[InstalledSkill]:
        """Scan a directory for installed skills, merging status from the index."""
        results: list[InstalledSkill] = []
        if not directory.exists():
            return results

        index = self._load_index()

        for entry in sorted(directory.iterdir()):
            skill_yaml = entry / "skill.yaml"
            if entry.is_dir() and skill_yaml.exists():
                try:
                    manifest = parse_skill_yaml(skill_yaml)
                    key = f"{agent}/{manifest.name}"
                    meta = index.get(key, {})
                    status = SkillStatus(meta.get("status", SkillStatus.INSTALLED.value))
                    results.append(
                        InstalledSkill(
                            manifest=manifest,
                            install_path=str(entry.resolve()),
                            agent=agent,
                            status=status,
                            socket_path=meta.get("socket_path", ""),
                            pid=meta.get("pid"),
                        )
                    )
                except (ValueError, FileNotFoundError):
                    continue

        return results

    def _load_index(self) -> dict:
        """Load the registry index from disk."""
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index(self, index: dict) -> None:
        """Persist the registry index to disk."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, indent=2, default=str))

    def _update_index(self, skill: InstalledSkill) -> None:
        """Add or update a skill in the registry index."""
        index = self._load_index()
        key = f"{skill.agent}/{skill.manifest.name}"
        index[key] = {
            "name": skill.manifest.name,
            "version": skill.manifest.version,
            "agent": skill.agent,
            "install_path": skill.install_path,
            "installed_at": skill.installed_at.isoformat(),
            "installed_by": skill.installed_by,
            "status": skill.status.value,
            "socket_path": skill.socket_path,
            "pid": skill.pid,
            "types": [t.value for t in skill.manifest.component_types],
        }
        self._save_index(index)

    def set_status(self, name: str, agent: str, status: "SkillStatus") -> bool:
        """Change the status of an installed skill (e.g. enable/disable).

        Args:
            name: Skill name.
            agent: Agent namespace.
            status: New status value.

        Returns:
            bool: True if the skill was found and updated.
        """
        target = self._skill_dir(name, agent)
        if not (target / "skill.yaml").exists():
            return False

        index = self._load_index()
        key = f"{agent}/{name}"
        if key not in index:
            # Build a minimal entry so we can persist the status
            index[key] = {
                "name": name,
                "agent": agent,
                "install_path": str(target),
                "status": status.value,
            }
        else:
            index[key]["status"] = status.value
        self._save_index(index)
        return True

    def search(self, query: str, agent: Optional[str] = None) -> list["InstalledSkill"]:
        """Search installed skills by name, description, or tags.

        Args:
            query: Case-insensitive search string.
            agent: Limit search to a specific agent namespace.

        Returns:
            list[InstalledSkill]: Matching skills.
        """
        q = query.lower()
        results = []
        for skill in self.list_skills(agent):
            m = skill.manifest
            if (
                q in m.name.lower()
                or q in m.description.lower()
                or any(q in t.lower() for t in m.tags)
            ):
                results.append(skill)
        return results

    def _remove_from_index(self, name: str, agent: str) -> None:
        """Remove a skill from the registry index."""
        index = self._load_index()
        key = f"{agent}/{name}"
        index.pop(key, None)
        self._save_index(index)
