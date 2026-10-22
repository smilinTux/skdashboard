"""SKSkills Remote Registry — publish, discover, and install skills from remote hubs.

Supports two transport modes:
  - HTTP registry: JSON API at a well-known URL (e.g., https://skills.smilintux.org/api)
  - Git repo: clone a skill repository and install from it

Remote registry protocol (HTTP):
    GET  /api/skills                 -> list all published skills
    GET  /api/skills/{name}          -> skill manifest + download URL
    GET  /api/skills/{name}/{version} -> specific version
    POST /api/skills                 -> publish a new skill (requires CapAuth)

Skill packages are tarballs: skill-name-0.1.0.tar.gz containing the skill directory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from .models import parse_skill_yaml

if TYPE_CHECKING:
    from .models import InstalledSkill

logger = logging.getLogger("skskills.remote")

DEFAULT_REGISTRY_URL = "https://skills.smilintux.org/api"


class RemoteSkillEntry(BaseModel):
    """A skill entry from the remote registry."""

    name: str
    version: str
    description: str = ""
    author: str = ""
    download_url: str = ""
    sha256: str = Field(default="", description="SHA-256 hash of the tarball")
    tags: list[str] = Field(default_factory=list)
    signed: bool = False
    signed_by: str = ""


class RegistryIndex(BaseModel):
    """The full remote registry index."""

    url: str
    skills: list[RemoteSkillEntry] = Field(default_factory=list)
    fetched_at: str = ""


class RemoteRegistry:
    """Client for the SKSkills remote registry.

    Handles fetching skill listings, downloading skill packages,
    and publishing skills to a remote hub.

    Args:
        registry_url: Base URL for the registry API.
        cache_dir: Local cache directory for downloaded packages.
    """

    def __init__(
        self,
        registry_url: str = DEFAULT_REGISTRY_URL,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self.cache_dir = cache_dir or Path("~/.skskills/cache/remote").expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_cache: Optional[RegistryIndex] = None

    def _http_get(self, url: str) -> Any:
        """Perform an HTTP GET request and return parsed JSON.

        Args:
            url: Full URL to fetch.

        Returns:
            Parsed JSON response.

        Raises:
            ConnectionError: If the request fails.
        """
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Failed to fetch {url}: {exc}") from exc

    def _http_download(self, url: str, dest: Path) -> Path:
        """Download a file from a URL.

        Args:
            url: URL to download.
            dest: Destination file path.

        Returns:
            Path to the downloaded file.

        Raises:
            ConnectionError: If the download fails.
        """
        import urllib.error
        import urllib.request

        try:
            urllib.request.urlretrieve(url, str(dest))
            return dest
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Failed to download {url}: {exc}") from exc

    def fetch_index(self, force: bool = False) -> RegistryIndex:
        """Fetch the remote skill index.

        Args:
            force: Bypass cache and re-fetch.

        Returns:
            RegistryIndex: The remote registry listing.
        """
        if self._index_cache and not force:
            return self._index_cache

        cache_file = self.cache_dir / "index.json"

        try:
            data = self._http_get(f"{self.registry_url}/skills")
            from datetime import datetime

            index = RegistryIndex(
                url=self.registry_url,
                skills=[RemoteSkillEntry(**s) for s in data.get("skills", data if isinstance(data, list) else [])],
                fetched_at=datetime.now().isoformat(),
            )
            cache_file.write_text(index.model_dump_json(indent=2))
            self._index_cache = index
            return index
        except ConnectionError:
            # Fall back to cached index
            if cache_file.exists():
                logger.warning("Remote unreachable, using cached index")
                self._index_cache = RegistryIndex.model_validate_json(cache_file.read_text())
                return self._index_cache
            raise

    def search(self, query: str) -> list[RemoteSkillEntry]:
        """Search the remote registry.

        Args:
            query: Case-insensitive search string.

        Returns:
            list[RemoteSkillEntry]: Matching remote skills.
        """
        index = self.fetch_index()
        q = query.lower()
        return [
            s for s in index.skills
            if q in s.name.lower()
            or q in s.description.lower()
            or any(q in t.lower() for t in s.tags)
        ]

    def get_skill_info(self, name: str, version: Optional[str] = None) -> Optional[RemoteSkillEntry]:
        """Get info about a specific remote skill.

        Args:
            name: Skill name.
            version: Specific version (latest if None).

        Returns:
            RemoteSkillEntry or None.
        """
        index = self.fetch_index()
        matches = [s for s in index.skills if s.name == name]
        if not matches:
            return None

        if version:
            versioned = [s for s in matches if s.version == version]
            return versioned[0] if versioned else None

        # Return latest version (simple string sort — semver would be better)
        matches.sort(key=lambda s: s.version, reverse=True)
        return matches[0]

    def download(self, name: str, version: Optional[str] = None) -> Path:
        """Download a skill package from the remote registry.

        Args:
            name: Skill name to download.
            version: Specific version (latest if None).

        Returns:
            Path: Path to the extracted skill directory.

        Raises:
            FileNotFoundError: If the skill isn't found.
            ValueError: If the checksum doesn't match.
        """
        entry = self.get_skill_info(name, version)
        if entry is None:
            raise FileNotFoundError(f"Skill not found in remote registry: {name}")

        if not entry.download_url:
            raise ValueError(f"No download URL for skill: {name} v{entry.version}")

        # Download tarball
        tarball_name = f"{entry.name}-{entry.version}.tar.gz"
        tarball_path = self.cache_dir / tarball_name
        self._http_download(entry.download_url, tarball_path)

        # Verify checksum
        if entry.sha256:
            actual_hash = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
            if actual_hash != entry.sha256:
                tarball_path.unlink()
                raise ValueError(
                    f"Checksum mismatch for {tarball_name}: "
                    f"expected {entry.sha256}, got {actual_hash}"
                )

        # Extract
        extract_dir = self.cache_dir / "extracted" / entry.name
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)

        with tarfile.open(tarball_path, "r:gz") as tar:
            # Security: prevent path traversal
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    raise ValueError(f"Unsafe path in tarball: {member.name}")
            tar.extractall(extract_dir, filter="data")

        # Find the skill.yaml — might be in a subdirectory
        skill_yaml = extract_dir / "skill.yaml"
        if not skill_yaml.exists():
            for child in extract_dir.iterdir():
                if child.is_dir() and (child / "skill.yaml").exists():
                    return child
            raise FileNotFoundError(f"No skill.yaml found in downloaded package: {name}")

        return extract_dir

    def pull(
        self,
        name: str,
        version: Optional[str] = None,
        agent: str = "global",
        force: bool = False,
    ) -> "InstalledSkill":
        """Download and install a skill from the remote registry.

        Convenience method that combines download + local install.

        Args:
            name: Skill name to pull.
            version: Specific version (latest if None).
            agent: Agent namespace.
            force: Overwrite existing installation.

        Returns:
            InstalledSkill: The installed skill metadata.
        """
        from .registry import SkillRegistry

        skill_dir = self.download(name, version)
        registry = SkillRegistry()
        return registry.install(skill_dir, agent=agent, force=force)

    @staticmethod
    def package(skill_dir: Path, output_dir: Optional[Path] = None) -> Path:
        """Package a skill directory into a distributable tarball.

        Args:
            skill_dir: Path to the skill directory.
            output_dir: Where to write the tarball (default: current directory).

        Returns:
            Path: Path to the created tarball.

        Raises:
            FileNotFoundError: If skill.yaml doesn't exist.
        """
        manifest = parse_skill_yaml(skill_dir / "skill.yaml")
        output_dir = output_dir or Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)

        tarball_name = f"{manifest.name}-{manifest.version}.tar.gz"
        tarball_path = output_dir / tarball_name

        with tarfile.open(tarball_path, "w:gz") as tar:
            for item in skill_dir.rglob("*"):
                # Skip hidden files, __pycache__, .venv
                rel = item.relative_to(skill_dir)
                parts = rel.parts
                if any(p.startswith(".") or p == "__pycache__" or p == ".venv" for p in parts):
                    continue
                tar.add(item, arcname=str(rel))

        sha256 = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
        logger.info("Packaged %s v%s -> %s (sha256: %s)",
                     manifest.name, manifest.version, tarball_path, sha256)

        # Write metadata sidecar
        meta_path = tarball_path.with_suffix(".json")
        meta_path.write_text(json.dumps({
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author.name,
            "sha256": sha256,
            "tags": manifest.tags,
            "signed": manifest.is_signed(),
            "signed_by": manifest.signed_by,
        }, indent=2))

        return tarball_path

    def publish(self, skill_dir: Path, token: Optional[str] = None) -> dict:
        """Publish a skill to the remote registry.

        Args:
            skill_dir: Path to the skill directory.
            token: Authentication token (CapAuth bearer token).

        Returns:
            dict: Server response.

        Raises:
            ConnectionError: If the upload fails.
            ValueError: If the skill is invalid.
        """
        import urllib.error
        import urllib.request

        manifest = parse_skill_yaml(skill_dir / "skill.yaml")

        # Package first
        with tempfile.TemporaryDirectory() as tmpdir:
            tarball = self.package(skill_dir, Path(tmpdir))
            meta_path = tarball.with_suffix(".json")
            meta = json.loads(meta_path.read_text())

        url = f"{self.registry_url}/skills"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = json.dumps({
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author.name,
            "tags": manifest.tags,
            "signed": manifest.is_signed(),
            "signed_by": manifest.signed_by,
            "sha256": meta["sha256"],
        }).encode()

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Failed to publish to {url}: {exc}") from exc

    @staticmethod
    def from_git(repo_url: str, dest: Optional[Path] = None) -> Path:
        """Clone a skill from a git repository.

        Args:
            repo_url: Git repository URL.
            dest: Where to clone (default: temp directory).

        Returns:
            Path: Path to the cloned skill directory.

        Raises:
            RuntimeError: If git clone fails.
        """
        import subprocess

        if dest is None:
            dest = Path(tempfile.mkdtemp(prefix="skskill-"))

        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr}")

        if not (dest / "skill.yaml").exists():
            # Check one level deep
            for child in dest.iterdir():
                if child.is_dir() and (child / "skill.yaml").exists():
                    return child
            raise FileNotFoundError(f"No skill.yaml in cloned repo: {repo_url}")

        return dest
