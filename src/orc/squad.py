"""Squad profile loader for the orc orchestrator.

A squad profile defines how many agents of each role may run in parallel.
Profiles are YAML files stored in ``orc/squads/`` (project-level) or in the
package's bundled ``squads/`` directory.

Usage::

    from orc.squad import load_squad, SquadConfig

    squad = load_squad("default")                      # package default
    squad = load_squad("broad", agents_dir=orc_dir)   # project-level first
    squad.count("coder")                               # → 1

Profile format::

    planner: 1          # must always be 1 — planning is serialised
    coder: 4            # up to 4 parallel coders
    qa: 2               # up to 2 parallel QA reviewers
    timeout_minutes: 120  # watchdog: kill stuck agents after this many minutes

Constraints
-----------
* ``planner`` must be exactly **1**.  Scaling planners would require branch
  isolation of the dev worktree; that complexity is intentionally deferred.
  The CLI raises an error if ``planner != 1`` so the user gets a clear message
  rather than undefined behaviour.
* All counts must be ``>= 1``.
* ``timeout_minutes`` must be ``>= 1``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Package-bundled squads directory (fallback when project-level squad not found).
_PACKAGE_SQUADS_DIR = Path(__file__).parent / "squads"

# Kept for backward compatibility — points to the package bundled squads.
SQUADS_DIR = _PACKAGE_SQUADS_DIR

_VALID_ROLES: frozenset[str] = frozenset({"planner", "coder", "qa"})
_DEFAULT_TIMEOUT_MINUTES = 120


@dataclass(frozen=True)
class SquadConfig:
    """Immutable snapshot of a parsed squad profile."""

    planner: int
    coder: int
    qa: int
    timeout_minutes: int

    def count(self, role: str) -> int:
        """Return the configured agent count for *role*."""
        if role not in _VALID_ROLES:
            raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(_VALID_ROLES)}")
        return getattr(self, role)


def _parse_squad_file(name: str, path: Path) -> SquadConfig:
    """Parse and validate a squad YAML file at *path*."""
    raw = yaml.safe_load(path.read_text()) or {}

    planner = int(raw.get("planner", 1))
    coder = int(raw.get("coder", 1))
    qa = int(raw.get("qa", 1))
    timeout_minutes = int(raw.get("timeout_minutes", _DEFAULT_TIMEOUT_MINUTES))

    if planner != 1:
        raise ValueError(
            f"Squad profile {name!r}: planner must be 1, got {planner}.\n"
            "Planning is serialised — only one planner is supported.\n"
            "Scale throughput by adding more coders and QA reviewers instead."
        )

    for role, cnt in [("coder", coder), ("qa", qa)]:
        if cnt < 1:
            raise ValueError(f"Squad profile {name!r}: {role} count must be >= 1, got {cnt}.")

    if timeout_minutes < 1:
        raise ValueError(
            f"Squad profile {name!r}: timeout_minutes must be >= 1, got {timeout_minutes}."
        )

    return SquadConfig(
        planner=planner,
        coder=coder,
        qa=qa,
        timeout_minutes=timeout_minutes,
    )


def load_squad(name: str, agents_dir: Path | None = None) -> SquadConfig:
    """Load and validate the squad profile *name*.

    Resolution order:
    1. ``{agents_dir}/squads/{name}.yaml`` if *agents_dir* is provided.
    2. Package bundled ``squads/{name}.yaml``.

    Raises:
        FileNotFoundError: Profile file not found in either location.
        ValueError: Profile contains invalid values (e.g. ``planner != 1``).
    """
    # Check project-level squads directory first.
    if agents_dir is not None:
        project_path = agents_dir / "squads" / f"{name}.yaml"
        if project_path.exists():
            return _parse_squad_file(name, project_path)

    # Fall back to package bundled squads.
    package_path = _PACKAGE_SQUADS_DIR / f"{name}.yaml"
    if package_path.exists():
        return _parse_squad_file(name, package_path)

    available = _list_available(agents_dir)
    raise FileNotFoundError(
        f"Squad profile {name!r} not found.\n"
        f"Available profiles: {available}\n"
        f"Create orc/squads/{name}.yaml to define a new profile."
    )


def list_squads(agents_dir: Path | None = None) -> list[str]:
    """Return sorted list of available squad profile names.

    If *agents_dir* is provided, returns profiles from ``agents_dir/squads/``.
    Otherwise returns profiles from the package bundled squads directory.
    """
    if agents_dir is not None:
        squads_dir = agents_dir / "squads"
    else:
        squads_dir = _PACKAGE_SQUADS_DIR

    if not squads_dir.exists():
        return []
    return sorted(p.stem for p in squads_dir.glob("*.yaml"))


def _list_available(agents_dir: Path | None = None) -> str:
    names = list_squads(agents_dir)
    if not names and agents_dir is not None:
        # Also check package squads for the error message
        names = list_squads()
    return ", ".join(names) if names else "(none)"
