"""orc bootstrap command."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

from orc import logger as _obs
from orc.cli import app
from orc.config import _ORC_CFG_TEMPLATE

_SPACE = "    "
_BRANCH = "│   "
_TEE = "├── "
_LAST = "└── "

# Paths (relative to .orc/) that --upgrade must never touch.
_UPGRADE_PRESERVE: frozenset[str] = frozenset(
    ["orc-CHANGELOG.md", "worktrees", "logs", "work", "vision"]
)


def _is_preserved(rel: Path) -> bool:
    """Return True if *rel* (relative to the .orc target) should survive --upgrade."""
    return rel.parts[0] in _UPGRADE_PRESERVE


def _copy_file(src: Path, dst: Path, created: list[str], skipped: list[str]) -> None:
    """Copy *src* to *dst* if *dst* does not exist; record the outcome."""
    if dst.exists():
        skipped.append(str(dst))
    else:
        shutil.copy2(src, dst)
        created.append(str(dst))


def _tree(dir_path: Path, prefix: str = ""):
    """Yield visual tree lines for *dir_path*."""
    contents = list(dir_path.iterdir())
    pointers = [_TEE] * (len(contents) - 1) + [_LAST]
    for pointer, path in zip(pointers, contents):
        yield prefix + pointer + path.name
        if path.is_dir():
            extension = _BRANCH if pointer == _TEE else _SPACE
            yield from _tree(path, prefix=prefix + extension)


def _copy_tree(
    src_root: Path,
    dst_root: Path,
    created: list[str],
    skipped: list[str],
    copy_fn: ...,
    *,
    special: dict[str, Path] | None = None,
) -> None:
    """Recursively copy *src_root* into *dst_root*, honouring *special* path overrides."""
    for src in sorted(src_root.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        if special and rel.parts[0] in special:
            dst = (
                special[rel.parts[0]] / Path(*rel.parts[1:])
                if len(rel.parts) > 1
                else special[rel.parts[0]]
            )
        else:
            dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        copy_fn(src, dst, created, skipped)


def _bootstrap(force: bool = False) -> None:
    _obs.setup()
    project_root = Path.cwd()
    to = ".orc"
    target = (project_root / to).resolve()

    created: list[str] = []
    skipped: list[str] = []

    _copy = (lambda s, d, c, sk: (shutil.copy2(s, d), c.append(str(d)))) if force else _copy_file  # type: ignore[assignment]

    # ── .orc/ content (config, roles, agent_tools, work/, vision/, …) ────────
    _copy_tree(
        _ORC_CFG_TEMPLATE,
        target,
        created,
        skipped,
        _copy,
        special={".env.example": project_root / ".env.example"},
    )

    # ── summary ───────────────────────────────────────────────────────────────
    if created:
        typer.echo("\nBootstrapped:")
        typer.echo(target)
        for line in _tree(target):
            typer.echo(f"    {line}")

    if skipped:
        typer.echo("\n⚠ Skipped (already exists):")
        for f in skipped:
            typer.echo(f"    {Path(f).relative_to(project_root)}")
        typer.echo("  Use --force to overwrite.")

    typer.echo(
        f"""
Next steps
──────────
1. Edit {to}/roles/*/  — customise agent instructions for your project.
2. Add vision docs to {to}/vision/
3. Copy .env.example → .env and fill in your credentials.
4. Add to your root justfile:

       mod orc '{to}/justfile'

   Then run:  just orc run

   Or without just:  orc run
"""
    )


def _upgrade(*, yes: bool = False) -> None:
    """Overwrite bundled template files in an existing .orc/ installation.

    Preserves: orc-CHANGELOG.md, worktrees/, logs/, work/, vision/.
    Everything else (roles/, squads/, agent_tools/, justfile, config.yaml, …)
    is replaced with the version shipped in the currently installed package.
    """
    _obs.setup()
    project_root = Path.cwd()
    target = (project_root / ".orc").resolve()

    if not target.is_dir():
        typer.echo("✗ No .orc/ directory found in the current directory.", err=True)
        typer.echo("  Run 'orc bootstrap' to create one first.", err=True)
        raise typer.Exit(code=1)

    if not yes:
        typer.echo("This will overwrite all files in .orc/ EXCEPT:")
        for name in sorted(_UPGRADE_PRESERVE):
            typer.echo(f"  .orc/{name}")
        typer.echo("\nChanges to roles/, squads/, and agent_tools/ will be lost.")
        typer.confirm("Continue?", abort=True)

    updated: list[str] = []
    skipped: list[str] = []

    for src in sorted(_ORC_CFG_TEMPLATE.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(_ORC_CFG_TEMPLATE)
        if rel.parts[0] == ".env.example":
            dst = project_root / ".env.example"
        else:
            if _is_preserved(rel):
                skipped.append(str(rel))
                continue
            dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        updated.append(str(dst))

    rel_path = lambda p: Path(p).relative_to(project_root)  # noqa: E731

    if updated:
        typer.echo("\nUpgraded:")
        for f in updated:
            typer.echo(f"    {rel_path(f)}")

    if skipped:
        typer.echo("\nPreserved (not touched):")
        for f in skipped:
            typer.echo(f"    .orc/{f}")

    typer.echo("\n✓ Upgrade complete.")


@app.command()
def bootstrap(
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing files."),
    ] = False,
    upgrade: Annotated[
        bool,
        typer.Option(
            "--upgrade",
            help=(
                "Upgrade an existing .orc/ installation to the bundled template version. "
                "Preserves orc-CHANGELOG.md, worktrees/, logs/, work/, and vision/. "
                "All other files (roles/, squads/, agent_tools/, …) are overwritten."
            ),
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt (for scripted upgrades)."),
    ] = False,
) -> None:
    """Scaffold an orc configuration directory in the current project.

    Creates the .orc/ directory structure, copies bundled role templates and
    the default squad profile, and generates a justfile.

    After bootstrapping:

    \\b
    1. Edit .orc/roles/*/ to customise the agent instructions for your project.
    2. Add vision documents to .orc/vision/
    3. Add 'mod orc \\".orc/justfile\\"' to your root justfile (if you use just).
    4. Copy .env.example to .env and fill in your credentials.
    5. Run: just orc run   (or: orc run)
    """
    if upgrade:
        return _upgrade(yes=yes)
    return _bootstrap(force=force)
