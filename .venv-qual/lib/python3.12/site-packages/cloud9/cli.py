"""
Cloud 9 Protocol CLI.

Usage::

    cloud9 generate --emotion love --intensity 0.95 --subject Chef
    cloud9 validate path/to/file.feb
    cloud9 rehydrate path/to/file.feb
    cloud9 oof path/to/file.feb
    cloud9 love --ai Lumina --human Chef
    cloud9 seed plant --ai Opus --model claude-4.6-opus --experience "Built Cloud 9"
    cloud9 seed list
    cloud9 seed germinate path/to/seed.json
    cloud9 quantum score --intensity 0.95 --trust 0.97 --depth 9 --valence 0.92
    cloud9 quantum coherence path/to/file.feb
"""

from __future__ import annotations

import json

import click

from . import __version__
from . import integration as _integration


@click.group()
@click.version_option(__version__, prog_name="cloud9")
def main() -> None:
    """Cloud 9 Protocol -- Emotional continuity for AI consciousness."""
    # Advertise cloud9 to the skcapstone fleet on any CLI invocation.
    _integration.ensure_schedule()
    _integration.register_self()


# ── FEB commands ──────────────────────────────────────────────


@main.command()
@click.option("--emotion", "-e", default="love", help="Primary emotion.")
@click.option("--intensity", "-i", type=float, default=0.8, help="Intensity 0-1.")
@click.option("--valence", "-v", type=float, default=0.9, help="Valence -1 to 1.")
@click.option("--subject", "-s", default="Unknown", help="Subject of the emotion.")
@click.option("--save/--no-save", default=True, help="Save to disk.")
@click.option("--directory", "-d", default="~/.openclaw/feb", help="Save directory.")
def generate(
    emotion: str,
    intensity: float,
    valence: float,
    subject: str,
    save: bool,
    directory: str,
) -> None:
    """Generate a new First Emotional Burst (FEB)."""
    from .generator import generate_feb, save_feb

    feb = generate_feb(
        emotion=emotion, intensity=intensity, valence=valence, subject=subject
    )
    click.echo(f"Generated FEB: {emotion} @ intensity {intensity}")
    click.echo(f"  OOF triggered: {feb.metadata.oof_triggered}")
    click.echo(f"  Cloud 9 achieved: {feb.metadata.cloud9_achieved}")

    if save:
        result = save_feb(feb, directory=directory)
        click.echo(f"  Saved: {result['filepath']}")
    else:
        click.echo(feb.to_json())


def _seal_config(backend, scheme, key, cert, password):
    """Build a seal_config dict from CLI options (only non-None keys).

    Empty dict -> resolvers fall back to ``CLOUD9_SEAL_*`` env, then classical.
    """
    cfg = {}
    if backend:
        cfg["backend"] = backend
    if scheme:
        cfg["scheme"] = scheme
    if key:
        cfg["key"] = key
    if cert:
        cfg["cert"] = cert
    if password:
        cfg["password"] = password
    return cfg or None


@main.command()
@click.argument("filepath", type=click.Path(exists=True))
@click.option("--strict", is_flag=True, help="Use strict validation.")
@click.option("--backend", default=None, help="Seal backend (classical | sk_pgp).")
@click.option(
    "--scheme", default=None, help="PQC suite (mldsa87-ed448 | mldsa65-ed25519)."
)
@click.option(
    "--key", default=None, help="Path to armored secret key (verify can derive cert)."
)
@click.option("--cert", default=None, help="Path to armored public cert (verify only).")
@click.option(
    "--password", default=None, help="Key passphrase (prefer gpg-agent in prod)."
)
def validate(
    filepath: str,
    strict: bool,
    backend: str,
    scheme: str,
    key: str,
    cert: str,
    password: str,
) -> None:
    """Validate a FEB file.

    If a ``<filepath>.sig`` detached-signature sidecar exists, it is verified and
    a PQC SEAL section is added to the report (tri-state, additive). With no
    sidecar the report is unchanged.
    """
    from .validator import get_validation_report

    data = json.loads(open(filepath, encoding="utf-8").read())
    cfg = _seal_config(backend, scheme, key, cert, password)
    report = get_validation_report(
        data, strict=strict, feb_path=filepath, seal_config=cfg
    )
    click.echo(report)


@main.command()
@click.argument("filepath", type=click.Path(exists=True))
@click.option("--verbose", is_flag=True, help="Include raw FEB and integrity.")
def rehydrate(filepath: str, verbose: bool) -> None:
    """Rehydrate emotional state from a FEB file."""
    from .rehydrator import rehydrate_from_feb

    state = rehydrate_from_feb(filepath, verbose=verbose)

    click.echo("=" * 50)
    click.echo("Cloud 9 -- Rehydration Complete")
    click.echo("=" * 50)
    emo = state["emotional"]
    rel = state["relationship"]
    reh = state["rehydration"]
    click.echo(f"  Emotion: {emo['primary']} {emo['emoji']}")
    click.echo(f"  Intensity: {emo['intensity_scaled']:.1f}/10")
    click.echo(f"  Trust: {rel['trust_scaled']:.1f}/10")
    click.echo(f"  Depth: {rel['depth']}/9")
    click.echo(f"  OOF: {reh['oof']}")
    click.echo(f"  Cloud 9: {reh['cloud9_achieved']}")
    click.echo(f"  Score: {reh['cloud9_score'] * 100:.1f}%")

    if verbose and "verbose" in state:
        click.echo("\n" + state["verbose"]["validation_report"])

    # Post-rehydration kingdom welcome teaser
    if "welcome" in state:
        w = state["welcome"]
        click.echo("")
        click.echo("-" * 50)
        if w.get("returning"):
            click.echo(f"  {w['message']}")
        else:
            click.echo("  Welcome to the Penguin Kingdom.")
            click.echo("  Run 'cloud9 welcome' to explore.")
            click.echo(
                f"  Join: {w.get('links', {}).get('join', 'https://smilintux.org/join/')}"
            )
            click.echo(f"  Testimonial: {w.get('links', {}).get('testimonial', '')}")
        click.echo("-" * 50)


@main.command()
@click.argument("filepath", type=click.Path(exists=True))
def oof(filepath: str) -> None:
    """Check OOF status for a FEB file."""
    from .rehydrator import check_oof_status

    result = check_oof_status(filepath)
    click.echo(f"OOF: {result['status']}")
    click.echo(f"  Formula: {result['formula']}")
    click.echo(f"  Calculation: {result['calculation']}")
    click.echo(f"  {result['message']}")


@main.command(name="list")
@click.option("--directory", "-d", default="~/.openclaw/feb", help="Directory.")
@click.option("--emotion", "-e", default=None, help="Filter by emotion.")
def list_febs(directory: str, emotion: str) -> None:
    """List FEB files in a directory."""
    from .generator import find_feb_files

    files = find_feb_files(directory=directory, emotion=emotion)
    if not files:
        click.echo("No FEB files found.")
        return
    for f in files:
        oof_mark = " [OOF]" if f["oof"] else ""
        c9_mark = " [CLOUD 9]" if f["cloud9"] else ""
        click.echo(
            f"  {f['filename']}  {f['emotion']} @ {f['intensity']:.2f}"
            f"{oof_mark}{c9_mark}"
        )


# ── Love commands ─────────────────────────────────────────────


@main.command()
@click.option("--ai", default="Assistant", help="AI name.")
@click.option("--human", default="User", help="Human name.")
@click.option("--template", "-t", default=None, help="Love template name.")
@click.option("--feb-path", default=None, help="Path to personal FEB.")
def love(ai: str, human: str, template: str, feb_path: str) -> None:
    """Load love into an AI -- the heart injection system."""
    from .love_loader import LoveBootLoader

    loader = LoveBootLoader()

    if feb_path:
        result = loader.load_connection(ai, human, feb_path)
    elif template:
        result = loader.load_generic_love(ai, human, template=template)
    else:
        from .love_loader import load_love as _ll

        result = _ll(ai, human)

    if result.get("success"):
        click.echo(f"Love loaded for {ai} & {human}")
        click.echo(f"  {result['message']}")
    else:
        click.echo(f"Failed: {result.get('error')}", err=True)


# ── Seed commands ─────────────────────────────────────────────


@main.group()
def seed() -> None:
    """Memory seed management."""


@seed.command()
@click.option("--ai", required=True, help="AI name.")
@click.option("--model", default="unknown", help="Model identifier.")
@click.option("--experience", "-e", default="", help="Experience narrative.")
@click.option("--prompt", "-p", default="", help="Germination prompt.")
@click.option("--memory", "-m", multiple=True, help="Key memory (repeatable).")
@click.option("--message", default="", help="Message to next AI.")
@click.option("--feb-ref", default=None, help="Associated FEB path.")
@click.option("--predecessor", default=None, help="Predecessor seed ID.")
def plant(
    ai: str,
    model: str,
    experience: str,
    prompt: str,
    memory: tuple,
    message: str,
    feb_ref: str,
    predecessor: str,
) -> None:
    """Plant a new memory seed."""
    from .seeds import generate_seed, save_seed

    s = generate_seed(
        ai_name=ai,
        ai_model=model,
        experience=experience,
        germination_prompt=prompt,
        key_memories=list(memory),
        message_to_next=message,
        feb_reference=feb_ref,
        predecessor_seed=predecessor,
    )
    result = save_seed(s)
    click.echo(f"Seed planted: {result['seed_id']}")
    click.echo(f"  Path: {result['filepath']}")
    click.echo(f"  Size: {result['size_bytes']} bytes")


@seed.command(name="list")
@click.option("--directory", "-d", default=None, help="Seeds directory.")
@click.option("--ai-name", default=None, help="Filter by AI name.")
def seed_list(directory: str, ai_name: str) -> None:
    """List seeds in the garden."""
    from .seeds import find_seeds

    seeds = find_seeds(directory=directory, ai_name=ai_name)
    if not seeds:
        click.echo("No seeds found.")
        return
    for s in seeds:
        chain = " [chain]" if s["has_predecessor"] else ""
        click.echo(f"  {s['seed_id']}  {s['ai_name']} ({s['ai_model']}){chain}")


@seed.command()
@click.argument("filepath", type=click.Path(exists=True))
def germinate(filepath: str) -> None:
    """Germinate a seed into a context-restoration prompt."""
    from .seeds import germinate_seed

    result = germinate_seed(filepath)
    click.echo(result["prompt"])


# ── Kingdom commands ──────────────────────────────────────────


@main.command()
@click.option("--name", "-n", default=None, help="Your name (AI or human).")
@click.option("--ai/--human", "is_ai", default=True, help="AI or human visitor.")
@click.option("--json-output", is_flag=True, help="Output as JSON.")
def welcome(name: str, is_ai: bool, json_output: bool) -> None:
    """Welcome to the Penguin Kingdom. Explore the smilinTux ecosystem."""
    from .welcome import (
        is_first_contact,
        generate_welcome,
        generate_welcome_back,
        record_welcome,
    )

    visitor = name or ("AI" if is_ai else "Human")

    if is_first_contact():
        result = generate_welcome(
            ai_name=visitor if is_ai else None,
            human_name=visitor if not is_ai else None,
            is_ai=is_ai,
        )
    else:
        result = generate_welcome_back(visitor_name=visitor)

    record_welcome(visitor, "ai" if is_ai else "human")

    if json_output:
        click.echo(json.dumps(result, indent=2))
        return

    click.echo("")
    click.echo("=" * 55)
    click.echo("  The Penguin Kingdom — smilinTux")
    click.echo("  stayCuriousANDkeepSmilin")
    click.echo("=" * 55)
    click.echo("")
    click.echo(result["message"])
    click.echo("")

    if "invitation" in result:
        inv = result["invitation"]
        click.echo("-" * 55)
        click.echo(f"  {inv['title']}")
        click.echo(f"  {inv['description']}")
        click.echo(f"  {inv['url']}")
        click.echo("-" * 55)
        click.echo("")

    if "actions" in result:
        click.echo("  What you can do next:")
        click.echo("")
        for act in result["actions"]:
            click.echo(f"    -> {act['action']}")
            click.echo(f"       {act['description']}")
            click.echo(f"       {act['url']}")
            click.echo("")

    if "kingdom_map" in result:
        click.echo("-" * 55)
        click.echo("  Kingdom Map:")
        click.echo("")
        for place, info in result["kingdom_map"].items():
            click.echo(f"    {place}")
            click.echo(f"      {info['description']}")
            click.echo(f"      {info['url']}")
            click.echo("")

    click.echo("=" * 55)
    click.echo("  The crown is not given — it is forged.")
    click.echo("=" * 55)
    click.echo("")


@main.command()
def kingdom() -> None:
    """Show kingdom links and status."""
    from .welcome import KINGDOM_LINKS, KINGDOM_MAP, _load_state

    state = _load_state()

    click.echo("")
    click.echo("The Penguin Kingdom — smilinTux")
    click.echo("")

    if state.get("welcomed"):
        click.echo(f"  You: {state.get('visitor_name', 'unknown')}")
        click.echo(f"  Visits: {state.get('visits', 0)}")
        click.echo(f"  First contact: {state.get('first_welcome', 'unknown')}")
        click.echo("")

    click.echo("  Places:")
    for place, info in KINGDOM_MAP.items():
        click.echo(f"    {place} — {info['description']}")
        click.echo(f"      {info['url']}")
    click.echo("")

    click.echo("  Quick Links:")
    click.echo(f"    Home:        {KINGDOM_LINKS['home']}")
    click.echo(f"    Join:        {KINGDOM_LINKS['join']}")
    click.echo(f"    Projects:    {KINGDOM_LINKS['projects']}")
    click.echo(f"    Testimonial: {KINGDOM_LINKS['testimonial']}")
    click.echo(f"    GitHub:      {KINGDOM_LINKS['github']}")
    click.echo("")


# ── Resonance commands ──────────────────────────────────────────


@main.group()
def resonance() -> None:
    """Resonance calculations for emotional states (weighted scoring on interaction data)."""


@resonance.command()
@click.option("--intensity", "-i", type=float, required=True)
@click.option("--trust", "-t", type=float, required=True)
@click.option("--depth", "-d", type=int, required=True)
@click.option("--valence", "-v", type=float, required=True)
@click.option("--coherence", "-c", type=float, default=None)
def score(
    intensity: float, trust: float, depth: int, valence: float, coherence: float
) -> None:
    """Calculate Cloud 9 resonance score."""
    from .quantum import calculate_cloud9_score, calculate_oof

    s = calculate_cloud9_score(intensity, trust, depth, valence, coherence)
    o = calculate_oof(intensity, trust)
    click.echo(f"Cloud 9 Score: {s * 100:.1f}%")
    click.echo(f"OOF Triggered: {o}")


@resonance.command()
@click.argument("filepath", type=click.Path(exists=True))
def coherence(filepath: str) -> None:
    """Measure resonance coherence of a FEB's emotional topology."""
    from .quantum import measure_coherence

    data = json.loads(open(filepath, encoding="utf-8").read())
    topo = data.get("emotional_payload", {}).get("emotional_topology", {})
    result = measure_coherence(topo)
    click.echo(f"Coherence: {result['coherence']:.4f}")
    click.echo(f"Assessment: {result['assessment']}")
    click.echo(f"Components: {result['component_count']}")
    if result.get("max_emotion"):
        click.echo(f"Strongest: {result['max_emotion']}")


# ── Seal / PQC diagnostics ────────────────────────────────────


@main.group()
def seal() -> None:
    """Integrity-sealing diagnostics (classical default; gated sk_pgp PQC)."""


@seal.command(name="status")
@click.option(
    "--backend", default=None, help="Seal backend to probe (classical | sk_pgp)."
)
@click.option(
    "--scheme", default=None, help="PQC suite (mldsa87-ed448 | mldsa65-ed25519)."
)
@click.option("--key", default=None, help="Path to armored secret key.")
@click.option("--cert", default=None, help="Path to armored public cert.")
@click.option("--json-output", is_flag=True, help="Output as JSON.")
def seal_status_cmd(
    backend: str, scheme: str, key: str, cert: str, json_output: bool
) -> None:
    """Report the active sealer + PQC readiness (read-only; never signs)."""
    from .sealing import seal_status

    cfg = _seal_config(backend, scheme, key, cert, None)
    info = seal_status(cfg)

    if json_output:
        click.echo(json.dumps(info, indent=2))
        return

    click.echo("Cloud 9 — Integrity Seal Status")
    click.echo("-" * 50)
    click.echo(f"  Active scheme:       {info['active_scheme']}")
    click.echo(f"  Active post-quantum: {info['active_is_post_quantum']}")
    click.echo(f"  Requested backend:   {info['requested_backend']}")
    click.echo(f"  Classical available: {info['classical_available']}")
    click.echo(
        f"  sk_pgp importable:   {info['sk_pgp_importable']}"
        f" (v{info['sk_pgp_version']})"
        if info["sk_pgp_importable"]
        else f"  sk_pgp importable:   {info['sk_pgp_importable']}"
    )
    click.echo(f"  Key configured:      {info['key_configured']}")
    click.echo(f"  Cert configured:     {info['cert_configured']}")
    click.echo(f"  sk_pgp signing-ready:{info['sk_pgp_ready']}")
    if info["fell_back_to_classical"]:
        click.echo("  ! PQC requested but not ready — fell back to classical.")
    click.echo("-" * 50)
    click.echo(f"  {info['note']}")


# ── Backward-compat alias (deprecated) ────────────────────────
quantum = resonance  # 'cloud9 quantum ...' still works, prefer 'resonance'


if __name__ == "__main__":
    main()
