"""SKSkills Catalog — curated first-party skill registry.

Reads ``catalog.yaml`` (shipped alongside this package) and exposes a
searchable list of known skills with their pip/npm/git coordinates.

Usage::

    from skskills.catalog import SkillCatalog

    catalog = SkillCatalog()
    entry = catalog.get("skseed")           # CatalogEntry or None
    results = catalog.search("logic")       # list[CatalogEntry]
    pip_pkg = catalog.pip_package("skseed") # "skseed"
"""

from __future__ import annotations

import importlib.resources
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_CATALOG_FILENAME = "catalog.yaml"


@dataclass
class CatalogEntry:
    """A single skill entry in the curated catalog."""

    name: str
    description: str = ""
    pip: str = ""           # pip install name (may differ from skill name)
    npm: str = ""           # npm package (optional, e.g. @smilintux/skseed)
    git: str = ""           # git clone URL
    git_path: str = ""      # sub-path within the git repo (for example skills)
    tags: list[str] = field(default_factory=list)
    category: str = "community"
    tools: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)

    @property
    def install_hint(self) -> str:
        """Short install command for display."""
        if self.pip:
            return f"skskills pip-install {self.pip}"
        if self.git:
            return f"skskills clone {self.git}"
        return "skskills install <path>"

    @property
    def is_pip_installable(self) -> bool:
        """True if this skill can be installed via pip."""
        return bool(self.pip)


def _locate_catalog() -> Path:
    """Find catalog.yaml — bundled inside the package or in the repo root."""
    # 1. Try importlib.resources (installed package, Python 3.9+)
    try:
        ref = importlib.resources.files("skskills").joinpath(_CATALOG_FILENAME)
        if ref.is_file():  # type: ignore[union-attr]
            return Path(str(ref))
    except Exception:
        pass

    # 2. Try alongside the package source (editable install / dev)
    here = Path(__file__).parent
    candidates = [
        here / _CATALOG_FILENAME,           # src/skskills/catalog.yaml
        here.parent / _CATALOG_FILENAME,    # src/catalog.yaml
        here.parent.parent / _CATALOG_FILENAME,   # repo root (skskills/catalog.yaml)
        here.parent.parent.parent / _CATALOG_FILENAME,  # one more up
    ]
    for c in candidates:
        if c.is_file():
            return c

    raise FileNotFoundError(
        f"Could not locate {_CATALOG_FILENAME}. "
        "Expected it at the skskills repo root or bundled in the package."
    )


class SkillCatalog:
    """Reads and queries the curated skill catalog.

    Args:
        catalog_path: Override the default catalog.yaml location.
    """

    def __init__(self, catalog_path: Optional[Path] = None) -> None:
        path = catalog_path or _locate_catalog()
        raw = yaml.safe_load(path.read_text())
        self._entries: dict[str, CatalogEntry] = {}
        for item in raw.get("skills", []):
            entry = CatalogEntry(
                name=item["name"],
                description=item.get("description", "").strip(),
                pip=item.get("pip", ""),
                npm=item.get("npm", ""),
                git=item.get("git", ""),
                git_path=item.get("git_path", ""),
                tags=item.get("tags", []),
                category=item.get("category", "community"),
                tools=item.get("tools", []),
                hooks=item.get("hooks", []),
            )
            self._entries[entry.name] = entry

    # ── Queries ───────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[CatalogEntry]:
        """Look up a skill by exact name.

        Args:
            name: Skill name (e.g. ``"skseed"``).

        Returns:
            CatalogEntry or None.
        """
        return self._entries.get(name)

    def list_all(self, category: Optional[str] = None) -> list[CatalogEntry]:
        """List all catalog entries, optionally filtered by category.

        Args:
            category: Filter by category (``"core"``, ``"transport"``, ``"example"``, …).

        Returns:
            List of CatalogEntry objects.
        """
        entries = list(self._entries.values())
        if category:
            entries = [e for e in entries if e.category == category]
        return entries

    def search(self, query: str) -> list[CatalogEntry]:
        """Search by name, description, or tags (case-insensitive).

        Args:
            query: Search term.

        Returns:
            Matching CatalogEntry objects.
        """
        q = query.lower()
        return [
            e for e in self._entries.values()
            if (
                q in e.name.lower()
                or q in e.description.lower()
                or any(q in t.lower() for t in e.tags)
            )
        ]

    def pip_package(self, name: str) -> Optional[str]:
        """Return the pip package name for a skill.

        Args:
            name: Skill name.

        Returns:
            PyPI package name or None.
        """
        entry = self._entries.get(name)
        return entry.pip if entry else None

    def pip_installable(self) -> list[CatalogEntry]:
        """Return all skills that can be installed via pip."""
        return [e for e in self._entries.values() if e.is_pip_installable]

    def categories(self) -> list[str]:
        """Return all unique category names in the catalog."""
        seen: list[str] = []
        for e in self._entries.values():
            if e.category not in seen:
                seen.append(e.category)
        return seen

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries
