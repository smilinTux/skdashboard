"""SKSkills Pip Bridge — discover skill.yaml inside installed pip packages.

Skills that ship as Python packages bundle their ``skill.yaml`` inside the
package directory (at ``<package>/data/skill.yaml`` or ``<package>/skill.yaml``).
This module finds and loads those manifests without requiring a separate install step.

Usage::

    from skskills.pip_bridge import find_pip_skill, list_pip_skills

    path = find_pip_skill("skseed")          # Path to skill.yaml or None
    skills = list_pip_skills()               # All pip-installed skills on PATH
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import InstalledSkill
    from .registry import SkillRegistry

logger = logging.getLogger(__name__)

# Search order for skill.yaml inside an installed pip package
_SKILL_YAML_CANDIDATES = [
    "data/skill.yaml",   # preferred: <pkg>/data/skill.yaml (matches skseed layout)
    "skill.yaml",        # fallback: <pkg>/skill.yaml
]

# Known pip packages that ship a skill.yaml.  Populated from catalog.yaml at runtime.
_KNOWN_SKILL_PACKAGES: list[str] = [
    "skseed",
    "skcomm",
    "skchat",
    "skcapstone",
]


def find_pip_skill(package_name: str) -> Optional[Path]:
    """Find the skill.yaml bundled inside an installed pip package.

    Searches for the manifest at ``<package_dir>/data/skill.yaml`` and then
    ``<package_dir>/skill.yaml``.  Works for both regular installs and
    editable (``pip install -e``) installs.

    Args:
        package_name: PyPI / importlib package name (e.g. ``"skseed"``).

    Returns:
        Absolute Path to the skill.yaml file, or None if not found.
    """
    spec = importlib.util.find_spec(package_name)
    if spec is None:
        logger.debug("pip_bridge: package %r not importable", package_name)
        return None

    locations: list[Path] = []

    if spec.submodule_search_locations:
        # Regular package — search_locations[0] is the package directory
        for loc in spec.submodule_search_locations:
            locations.append(Path(loc))
    elif spec.origin:
        # Single-file module — use its parent
        locations.append(Path(spec.origin).parent)

    for pkg_dir in locations:
        for candidate in _SKILL_YAML_CANDIDATES:
            path = pkg_dir / candidate
            if path.is_file():
                logger.debug("pip_bridge: found skill.yaml for %r at %s", package_name, path)
                return path

    logger.debug("pip_bridge: no skill.yaml found in package %r", package_name)
    return None


def list_pip_skills(packages: Optional[list[str]] = None) -> list[tuple[str, Path]]:
    """Scan a list of pip packages for bundled skill.yaml manifests.

    Args:
        packages: Package names to check.  Defaults to ``_KNOWN_SKILL_PACKAGES``.

    Returns:
        List of ``(package_name, skill_yaml_path)`` tuples for every package
        that has a bundled manifest.
    """
    candidates = packages if packages is not None else _KNOWN_SKILL_PACKAGES
    found: list[tuple[str, Path]] = []
    for pkg in candidates:
        path = find_pip_skill(pkg)
        if path is not None:
            found.append((pkg, path))
    return found


def install_from_pip(
    package_name: str,
    registry: SkillRegistry,
    agent: str = "global",
    force: bool = False,
    pip_install: bool = True,
) -> InstalledSkill:
    """pip-install a package (if needed) and register its bundled skill.

    This is the one-shot command behind ``skskills pip-install <name>``:

    1. Optionally runs ``pip install <package_name>`` (skipped if the package
       is already importable and ``pip_install=False``).
    2. Finds the bundled ``skill.yaml``.
    3. Copies the skill into ``~/.skskills/installed/`` via the registry.

    Args:
        package_name: PyPI package name (e.g. ``"skseed"``).
        registry: A ``SkillRegistry`` instance.
        agent: Target agent namespace (default: ``"global"``).
        force: Overwrite an existing installation.
        pip_install: Run ``pip install`` even if the package is already present.

    Returns:
        InstalledSkill metadata.

    Raises:
        RuntimeError: If pip install fails or no skill.yaml is found.
    """
    import subprocess
    import sys

    # 1. pip install if requested or package not yet importable
    already_importable = importlib.util.find_spec(package_name) is not None
    if pip_install or not already_importable:
        logger.info("pip_bridge: pip install %s", package_name)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", package_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pip install {package_name!r} failed:\n{result.stderr or result.stdout}"
            )
        # Reload the package spec after install
        importlib.invalidate_caches()

    # 2. Locate skill.yaml
    skill_yaml = find_pip_skill(package_name)
    if skill_yaml is None:
        raise RuntimeError(
            f"Package {package_name!r} does not bundle a skill.yaml. "
            f"Cannot register as a skill."
        )

    # 3. Register via the registry (copy skill dir into ~/.skskills/installed/)
    return _register_from_skill_yaml(skill_yaml, registry, agent=agent, force=force)


def _register_from_skill_yaml(
    skill_yaml: Path,
    registry: SkillRegistry,
    agent: str,
    force: bool,
) -> InstalledSkill:
    """Register a skill given the absolute path to its skill.yaml.

    Copies the skill directory (parent of skill.yaml) into the registry.

    Args:
        skill_yaml: Absolute path to the skill.yaml file.
        registry: SkillRegistry instance.
        agent: Target agent namespace.
        force: Overwrite existing installation.

    Returns:
        InstalledSkill metadata.
    """
    import shutil
    import tempfile

    # Determine the source directory — the one that contains skill.yaml
    source_dir = skill_yaml.parent

    # Build a temporary staging directory with skill.yaml at root
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy2(skill_yaml, tmp_path / "skill.yaml")

        # Copy knowledge/ if present alongside skill.yaml
        knowledge_src = source_dir / "knowledge"
        if knowledge_src.is_dir():
            shutil.copytree(knowledge_src, tmp_path / "knowledge")

        installed = registry.install(tmp_path, agent=agent, force=force)

    return installed
