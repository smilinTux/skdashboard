"""SKSkills CLI — sovereign skill management from the terminal.

Commands:
    init        Scaffold a new skill project
    install     Install a skill from local path or URL
    list        Show installed skills
    info        Get detailed skill information
    run         Start the skill aggregator MCP server
    uninstall   Remove an installed skill
    link        Link a global skill to an agent
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .models import (
    KnowledgePack,
    SkillAuthor,
    SkillManifest,
    SkillStatus,
    generate_skill_yaml,
)
from .registry import SkillRegistry

console = Console()


@click.group()
@click.version_option(__version__, prog_name="skskills")
def main() -> None:
    """SKSkills — Sovereign Agent Skills Framework.

    MCP-native skill management replacing OpenClaw.
    Install, run, and manage skills that extend sovereign agents.
    """


@main.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Parent directory for the skill.")
@click.option("--author", default="", help="Author name.")
@click.option("--description", "desc", default="", help="Skill description.")
def init(name: str, directory: str, author: str, desc: str) -> None:
    """Scaffold a new skill project.

    Creates the directory structure and a starter skill.yaml.
    """
    base = Path(directory) / name
    if base.exists():
        console.print(f"[red]Directory already exists:[/red] {base}")
        sys.exit(1)

    base.mkdir(parents=True)
    (base / "knowledge").mkdir()
    (base / "tools").mkdir()
    (base / "hooks").mkdir()

    skill_md = base / "knowledge" / "SKILL.md"
    skill_md.write_text(f"# {name}\n\n{desc or 'A sovereign agent skill.'}\n")

    manifest = SkillManifest(
        name=name,
        version="0.1.0",
        description=desc or f"{name} skill for sovereign agents",
        author=SkillAuthor(name=author or "unknown"),
        knowledge=[
            KnowledgePack(
                path="knowledge/SKILL.md",
                description=f"Core knowledge for {name}",
                auto_load=True,
            ),
        ],
        tags=["sovereign"],
    )

    yaml_path = base / "skill.yaml"
    yaml_path.write_text(generate_skill_yaml(manifest))

    console.print(f"\n[green]Skill scaffolded:[/green] {base}")
    console.print("  skill.yaml       — manifest")
    console.print("  knowledge/       — context files (SKILL.md)")
    console.print("  tools/           — MCP tool scripts")
    console.print("  hooks/           — event-driven scripts")
    console.print(f"\nNext: edit skill.yaml, add tools, then [cyan]skskills install {base}[/cyan]")


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--agent", default="global", help="Agent namespace (default: global).")
@click.option("--force", is_flag=True, help="Overwrite existing installation.")
def install(source: str, agent: str, force: bool) -> None:
    """Install a skill from a local directory."""
    registry = SkillRegistry()
    try:
        installed = registry.install(Path(source), agent=agent, force=force)
        console.print(f"\n[green]Installed:[/green] {installed.manifest.name} v{installed.manifest.version}")
        console.print(f"  Agent: {installed.agent}")
        console.print(f"  Path:  {installed.install_path}")
        types = ", ".join(t.value for t in installed.manifest.component_types)
        console.print(f"  Types: {types}")
        if installed.manifest.tools:
            console.print(f"  Tools: {', '.join(installed.manifest.tool_names)}")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Install failed:[/red] {exc}")
        sys.exit(1)


@main.command("list")
@click.option("--agent", default=None, help="Filter by agent name.")
def list_skills(agent: str | None) -> None:
    """Show installed skills."""
    registry = SkillRegistry()
    skills = registry.list_skills(agent)

    if not skills:
        console.print("[dim]No skills installed.[/dim]")
        return

    table = Table(title="Installed Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Agent", style="green")
    table.add_column("Types")
    table.add_column("Tools", style="yellow")
    table.add_column("Status")
    table.add_column("Signed", style="magenta")

    for s in skills:
        types = ", ".join(t.value for t in s.manifest.component_types)
        tools = ", ".join(s.manifest.tool_names) or "-"
        signed = "yes" if s.manifest.is_signed() else "no"
        if s.status == SkillStatus.DISABLED:
            status_str = "[dim]disabled[/dim]"
        elif s.status == SkillStatus.RUNNING:
            status_str = "[green]running[/green]"
        else:
            status_str = "[cyan]enabled[/cyan]"
        table.add_row(
            s.manifest.name,
            s.manifest.version,
            s.agent,
            types,
            tools,
            status_str,
            signed,
        )

    console.print(table)


@main.command()
@click.argument("name")
@click.option("--agent", default="global", help="Agent namespace.")
def info(name: str, agent: str) -> None:
    """Get detailed information about a skill."""
    registry = SkillRegistry()
    skill = registry.get(name, agent)
    if skill is None:
        console.print(f"[red]Skill not found:[/red] {name} (agent: {agent})")
        sys.exit(1)

    m = skill.manifest
    console.print(f"\n[cyan bold]{m.name}[/cyan bold] v{m.version}")
    console.print(f"  {m.description}")
    console.print(f"  Author: {m.author.name}")
    if m.author.fingerprint:
        console.print(f"  Fingerprint: {m.author.fingerprint}")

    if m.knowledge:
        console.print("\n  [bold]Knowledge Packs:[/bold]")
        for k in m.knowledge:
            auto = " (auto-load)" if k.auto_load else ""
            console.print(f"    {k.path}{auto} — {k.description}")

    if m.tools:
        console.print("\n  [bold]Tools:[/bold]")
        for t in m.tools:
            console.print(f"    {m.name}.{t.name} — {t.description}")

    if m.hooks:
        console.print("\n  [bold]Hooks:[/bold]")
        for h in m.hooks:
            console.print(f"    {h.event.value} — {h.description}")

    if m.tags:
        console.print(f"\n  Tags: {', '.join(m.tags)}")

    signed = "[green]yes[/green]" if m.is_signed() else "[dim]no[/dim]"
    console.print(f"  Signed: {signed}")


@main.command()
@click.argument("name")
@click.option("--agent", default="global", help="Agent namespace.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def uninstall(name: str, agent: str, yes: bool) -> None:
    """Remove an installed skill."""
    if not yes:
        if not click.confirm(f"Uninstall '{name}' from agent '{agent}'?"):
            return

    registry = SkillRegistry()
    if registry.uninstall(name, agent):
        console.print(f"[green]Uninstalled:[/green] {name} (agent: {agent})")
    else:
        console.print(f"[red]Not found:[/red] {name} (agent: {agent})")
        sys.exit(1)


@main.command()
@click.argument("name")
@click.argument("agent")
def link(name: str, agent: str) -> None:
    """Link a global skill to a specific agent."""
    registry = SkillRegistry()
    try:
        path = registry.link_to_agent(name, agent)
        console.print(f"[green]Linked:[/green] {name} -> {agent} ({path})")
    except FileNotFoundError as exc:
        console.print(f"[red]Link failed:[/red] {exc}")
        sys.exit(1)


@main.command()
@click.argument("query")
@click.option("--agent", default=None, help="Limit search to a specific agent namespace.")
def search(query: str, agent: str | None) -> None:
    """Search installed skills by name, description, or tag."""
    registry = SkillRegistry()
    results = registry.search(query, agent)

    if not results:
        console.print(f"[dim]No skills found matching '{query}'.[/dim]")
        return

    table = Table(title=f"Search: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Agent", style="green")
    table.add_column("Description")
    table.add_column("Tags", style="yellow")

    for s in results:
        table.add_row(
            s.manifest.name,
            s.manifest.version,
            s.agent,
            s.manifest.description[:60] + ("..." if len(s.manifest.description) > 60 else ""),
            ", ".join(s.manifest.tags) or "-",
        )

    console.print(table)


@main.command()
@click.argument("name")
@click.option("--agent", default="global", help="Agent namespace.")
def enable(name: str, agent: str) -> None:
    """Enable a previously disabled skill."""
    registry = SkillRegistry()
    if registry.set_status(name, agent, SkillStatus.INSTALLED):
        console.print(f"[green]Enabled:[/green] {name} (agent: {agent})")
    else:
        console.print(f"[red]Not found:[/red] {name} (agent: {agent})")
        sys.exit(1)


@main.command()
@click.argument("name")
@click.option("--agent", default="global", help="Agent namespace.")
def disable(name: str, agent: str) -> None:
    """Disable a skill without uninstalling it."""
    registry = SkillRegistry()
    if registry.set_status(name, agent, SkillStatus.DISABLED):
        console.print(f"[yellow]Disabled:[/yellow] {name} (agent: {agent})")
    else:
        console.print(f"[red]Not found:[/red] {name} (agent: {agent})")
        sys.exit(1)


@main.command()
@click.argument("name")
@click.argument("source", type=click.Path(exists=True))
@click.option("--agent", default="global", help="Agent namespace.")
def update(name: str, source: str, agent: str) -> None:
    """Reinstall a skill from a (possibly updated) source directory.

    NAME is the existing skill name; SOURCE is the path to the updated skill.
    """
    registry = SkillRegistry()
    try:
        installed = registry.install(Path(source), agent=agent, force=True)
        console.print(f"\n[green]Updated:[/green] {installed.manifest.name} v{installed.manifest.version}")
        console.print(f"  Agent: {installed.agent}")
        console.print(f"  Path:  {installed.install_path}")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Update failed:[/red] {exc}")
        sys.exit(1)


@main.command()
@click.option("--agent", default="global", help="Agent namespace to load skills for.")
def run(agent: str) -> None:
    """Start the SKSkills aggregator MCP server.

    Loads all installed skills and exposes them via MCP on stdio.
    """
    import asyncio

    from .aggregator import SkillAggregator

    agg = SkillAggregator(agent=agent)
    count = agg.load_all_skills()
    console.print(f"[green]SKSkills aggregator:[/green] {count} skills loaded for '{agent}'")
    console.print("[dim]Serving on stdio (MCP protocol)...[/dim]")
    asyncio.run(agg.run_stdio())


# ── Remote registry commands ──────────────────────────────────────────


@main.command()
@click.argument("query")
@click.option("--registry", default=None, help="Remote registry URL.")
def remote_search(query: str, registry: str | None) -> None:
    """Search the remote skill registry."""
    from .remote import RemoteRegistry

    remote = RemoteRegistry(registry) if registry else RemoteRegistry()
    try:
        results = remote.search(query)
    except ConnectionError as exc:
        console.print(f"[red]Connection failed:[/red] {exc}")
        sys.exit(1)

    if not results:
        console.print(f"[dim]No remote skills found matching '{query}'.[/dim]")
        return

    table = Table(title=f"Remote: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Author", style="green")
    table.add_column("Description")
    table.add_column("Tags", style="yellow")
    table.add_column("Signed", style="magenta")

    for s in results:
        table.add_row(
            s.name,
            s.version,
            s.author,
            s.description[:60] + ("..." if len(s.description) > 60 else ""),
            ", ".join(s.tags) or "-",
            "yes" if s.signed else "no",
        )

    console.print(table)


@main.command()
@click.argument("name")
@click.option("--version", "ver", default=None, help="Specific version to pull.")
@click.option("--agent", default="global", help="Agent namespace.")
@click.option("--force", is_flag=True, help="Overwrite existing installation.")
@click.option("--registry", default=None, help="Remote registry URL.")
def pull(name: str, ver: str | None, agent: str, force: bool, registry: str | None) -> None:
    """Download and install a skill from the remote registry."""
    from .remote import RemoteRegistry

    remote = RemoteRegistry(registry) if registry else RemoteRegistry()
    try:
        installed = remote.pull(name, version=ver, agent=agent, force=force)
        console.print(f"\n[green]Pulled:[/green] {installed.manifest.name} v{installed.manifest.version}")
        console.print(f"  Agent: {installed.agent}")
        console.print(f"  Path:  {installed.install_path}")
        types = ", ".join(t.value for t in installed.manifest.component_types)
        console.print(f"  Types: {types}")
    except (ConnectionError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Pull failed:[/red] {exc}")
        sys.exit(1)


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--registry", default=None, help="Remote registry URL.")
@click.option("--token", envvar="SKSKILLS_TOKEN", help="CapAuth bearer token.")
def publish(source: str, registry: str | None, token: str | None) -> None:
    """Publish a skill to the remote registry."""
    from .remote import RemoteRegistry

    remote = RemoteRegistry(registry) if registry else RemoteRegistry()
    try:
        result = remote.publish(Path(source), token=token)
        console.print("\n[green]Published![/green]")
        if isinstance(result, dict):
            console.print(f"  Name:    {result.get('name', '?')}")
            console.print(f"  Version: {result.get('version', '?')}")
            if result.get("url"):
                console.print(f"  URL:     {result['url']}")
    except (ConnectionError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Publish failed:[/red] {exc}")
        sys.exit(1)


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--output", "-o", default=".", help="Output directory for the tarball.")
def package(source: str, output: str) -> None:
    """Package a skill into a distributable tarball."""
    from .remote import RemoteRegistry

    try:
        tarball = RemoteRegistry.package(Path(source), Path(output))
        sha256_hash = __import__("hashlib").sha256(tarball.read_bytes()).hexdigest()
        console.print(f"\n[green]Packaged:[/green] {tarball}")
        console.print(f"  SHA-256: {sha256_hash}")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Package failed:[/red] {exc}")
        sys.exit(1)


@main.command()
@click.argument("repo_url")
@click.option("--agent", default="global", help="Agent namespace.")
@click.option("--force", is_flag=True, help="Overwrite existing installation.")
def clone(repo_url: str, agent: str, force: bool) -> None:
    """Install a skill from a git repository."""
    from .registry import SkillRegistry
    from .remote import RemoteRegistry

    try:
        skill_dir = RemoteRegistry.from_git(repo_url)
        registry = SkillRegistry()
        installed = registry.install(skill_dir, agent=agent, force=force)
        console.print(f"\n[green]Cloned & installed:[/green] {installed.manifest.name} v{installed.manifest.version}")
        console.print(f"  Agent: {installed.agent}")
        console.print(f"  Path:  {installed.install_path}")
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Clone failed:[/red] {exc}")
        sys.exit(1)


# ── Pip-based install ─────────────────────────────────────────────────────────


@main.command("pip-install")
@click.argument("package")
@click.option("--agent", default="global", help="Agent namespace (default: global).")
@click.option("--force", is_flag=True, help="Overwrite existing installation.")
@click.option(
    "--no-pip",
    is_flag=True,
    default=False,
    help="Skip running pip install (package must already be installed).",
)
def pip_install(package: str, agent: str, force: bool, no_pip: bool) -> None:
    """Install a skill from a pip package.

    Runs ``pip install <PACKAGE>``, finds the bundled skill.yaml inside the
    package, and registers the skill locally.

    Examples:

      skskills pip-install skseed
      skskills pip-install skcapstone --agent lumina
      skskills pip-install skseed --no-pip   # already installed, just register
    """
    from .pip_bridge import install_from_pip

    registry = SkillRegistry()
    console.print(f"\n  [cyan]pip-install:[/cyan] {package}")

    try:
        installed = install_from_pip(
            package,
            registry,
            agent=agent,
            force=force,
            pip_install=not no_pip,
        )
        console.print(f"  [green]Installed:[/green] {installed.manifest.name} v{installed.manifest.version}")
        console.print(f"  Agent:  {installed.agent}")
        console.print(f"  Path:   {installed.install_path}")
        if installed.manifest.tools:
            console.print(f"  Tools:  {', '.join(installed.manifest.tool_names)}")
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        console.print(f"  [red]pip-install failed:[/red] {exc}")
        sys.exit(1)


# ── Catalog commands ──────────────────────────────────────────────────────────


@main.group()
def catalog() -> None:
    """Browse and install skills from the curated catalog.

    The catalog lists all first-party smilintux-org skills with their
    pip/npm/git coordinates.  Skills are independent pip packages — no
    git submodules required.

    Examples:

      skskills catalog list
      skskills catalog info skseed
      skskills catalog install skseed
      skskills catalog search logic
    """


@catalog.command("list")
@click.option("--category", default=None, help="Filter by category (core, transport, example).")
def catalog_list(category: str | None) -> None:
    """List all skills in the curated catalog."""
    from .catalog import SkillCatalog

    try:
        cat = SkillCatalog()
    except FileNotFoundError as exc:
        console.print(f"[red]Catalog not found:[/red] {exc}")
        sys.exit(1)

    entries = cat.list_all(category=category)
    if not entries:
        console.print("[dim]No catalog entries found.[/dim]")
        return

    table = Table(title="SKSkills Catalog" + (f" — {category}" if category else ""))
    table.add_column("Name", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Pip", style="yellow")
    table.add_column("Description")
    table.add_column("Tags", style="dim")

    for e in entries:
        table.add_row(
            e.name,
            e.category,
            e.pip or "-",
            (e.description[:55] + "…") if len(e.description) > 55 else e.description,
            ", ".join(e.tags[:4]) + ("…" if len(e.tags) > 4 else ""),
        )

    console.print(table)
    console.print(
        "\n  [dim]Install any skill:[/dim] [cyan]skskills catalog install <name>[/cyan]"
    )


@catalog.command("info")
@click.argument("name")
def catalog_info(name: str) -> None:
    """Show detailed information about a catalog skill."""
    from .catalog import SkillCatalog

    cat = SkillCatalog()
    entry = cat.get(name)
    if entry is None:
        console.print(f"[red]Not in catalog:[/red] {name}")
        console.print("  Try [cyan]skskills catalog search <term>[/cyan] to find it.")
        sys.exit(1)

    console.print(f"\n[cyan bold]{entry.name}[/cyan bold]  [{entry.category}]")
    console.print(f"  {entry.description}")
    if entry.pip:
        console.print(f"\n  [bold]pip:[/bold]    pip install {entry.pip}")
    if entry.npm:
        console.print(f"  [bold]npm:[/bold]    npm install {entry.npm}")
    if entry.git:
        console.print(f"  [bold]git:[/bold]    {entry.git}")
    if entry.tools:
        console.print(f"\n  [bold]Tools ({len(entry.tools)}):[/bold]  {', '.join(entry.tools)}")
    if entry.hooks:
        console.print(f"  [bold]Hooks:[/bold]  {', '.join(entry.hooks)}")
    console.print(f"\n  [bold]Tags:[/bold]   {', '.join(entry.tags)}")
    console.print(f"\n  [dim]Install:[/dim] {entry.install_hint}")


@catalog.command("search")
@click.argument("query")
def catalog_search(query: str) -> None:
    """Search the catalog by name, description, or tag."""
    from .catalog import SkillCatalog

    cat = SkillCatalog()
    results = cat.search(query)

    if not results:
        console.print(f"[dim]No catalog entries matching '{query}'.[/dim]")
        return

    table = Table(title=f"Catalog search: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Install", style="yellow")
    table.add_column("Description")

    for e in results:
        table.add_row(
            e.name,
            e.category,
            e.install_hint,
            (e.description[:60] + "…") if len(e.description) > 60 else e.description,
        )

    console.print(table)


@catalog.command("install")
@click.argument("name")
@click.option("--agent", default="global", help="Agent namespace (default: global).")
@click.option("--force", is_flag=True, help="Overwrite existing installation.")
def catalog_install(name: str, agent: str, force: bool) -> None:
    """Install a skill from the catalog.

    Looks up the skill in catalog.yaml, then runs the appropriate
    install method (pip-install for pip packages, clone for git-only skills).
    """
    from .catalog import SkillCatalog
    from .pip_bridge import install_from_pip

    cat = SkillCatalog()
    entry = cat.get(name)
    if entry is None:
        console.print(f"[red]Not in catalog:[/red] {name}")
        console.print("  Try [cyan]skskills catalog search <term>[/cyan]")
        sys.exit(1)

    registry = SkillRegistry()

    if entry.is_pip_installable:
        console.print(f"\n  [cyan]catalog install:[/cyan] {name} (via pip: {entry.pip})")
        try:
            installed = install_from_pip(entry.pip, registry, agent=agent, force=force)
            console.print(f"  [green]Installed:[/green] {installed.manifest.name} v{installed.manifest.version}")
            console.print(f"  Agent:  {installed.agent}")
            if installed.manifest.tools:
                console.print(f"  Tools:  {', '.join(installed.manifest.tool_names)}")
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            console.print(f"  [red]Install failed:[/red] {exc}")
            sys.exit(1)

    elif entry.git:
        # Fall back to git clone
        console.print(f"\n  [cyan]catalog install:[/cyan] {name} (via git: {entry.git})")
        from .remote import RemoteRegistry

        try:
            git_url = entry.git
            if entry.git_path:
                # Subdirectory skill — clone and install from subdir
                import subprocess
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    subprocess.run(
                        ["git", "clone", "--depth=1", git_url, tmp],
                        check=True,
                        capture_output=True,
                    )
                    skill_dir = Path(tmp) / entry.git_path
                    installed = registry.install(skill_dir, agent=agent, force=force)
            else:
                skill_dir = RemoteRegistry.from_git(git_url)
                installed = registry.install(skill_dir, agent=agent, force=force)

            console.print(f"  [green]Installed:[/green] {installed.manifest.name}")
        except Exception as exc:
            console.print(f"  [red]Install failed:[/red] {exc}")
            sys.exit(1)

    else:
        console.print(f"[yellow]No install method available for '{name}'.[/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    main()
