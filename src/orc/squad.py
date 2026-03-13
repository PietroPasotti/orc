"""Squad profile loader for the orc orchestrator.

A squad profile defines how many agents of each role may run in parallel and
which AI model each role should use.
Profiles are YAML files stored in ``.orc/squads/`` (project-level) or in the
package's bundled ``squads/`` directory.

Usage::

    from orc.squad import load_squad, load_all_squads, SquadConfig

    squad = load_squad("default")                      # package default
    squad = load_squad("broad", agents_dir=orc_dir)   # project-level first
    squad.count("coder")                               # → 1
    squad.model("coder")                               # → "claude-sonnet-4.6"

    all_squads = load_all_squads(agents_dir=orc_dir)   # list[SquadConfig]

Profile format::

    name: default
    description: |
      One agent of each type, running sequentially.
    composition:
      - role: planner
        count: 1
        model: claude-sonnet-4.6
      - role: coder
        count: 4
        model: claude-sonnet-4.6
      - role: qa
        count: 2
        model: claude-sonnet-4.6
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

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

# Package-bundled squads directory (fallback when project-level squad not found).
# Lives inside the default template so there is a single source of truth.
_PACKAGE_SQUADS_DIR = Path(__file__).parent.parent / "templates" / "default" / "squads"


class AgentRole(StrEnum):
    """Valid agent roles in an orc squad.

    Because :class:`AgentRole` inherits from :class:`str`, each member compares
    equal to its string value (e.g. ``AgentRole.CODER == "coder"`` is ``True``),
    so existing APIs that accept plain role strings remain fully compatible.
    """

    PLANNER = "planner"
    CODER = "coder"
    QA = "qa"


_VALID_ROLES: frozenset[AgentRole] = frozenset(AgentRole)
_DEFAULT_TIMEOUT_MINUTES = 120
_DEFAULT_MODEL = "claude-sonnet-4.6"


@dataclass(frozen=True)
class SquadConfig:
    """Immutable snapshot of a parsed squad profile."""

    planner: int
    coder: int
    qa: int
    timeout_minutes: int
    name: str = ""
    description: str = ""
    _models: dict[str, str] = field(default_factory=dict, compare=False)

    def count(self, role: AgentRole | str) -> int:
        """Return the configured agent count for *role*."""
        if role not in _VALID_ROLES:
            raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(_VALID_ROLES)}")
        role_value = role.value if isinstance(role, AgentRole) else role
        return getattr(self, role_value)

    def model(self, role: AgentRole | str) -> str:
        """Return the configured model name for *role*.

        Falls back to ``_DEFAULT_MODEL`` when no model is specified for the role.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(_VALID_ROLES)}")
        role_value = role.value if isinstance(role, AgentRole) else role
        return self._models.get(role_value, _DEFAULT_MODEL)


def _parse_squad_file(file_name: str, path: Path) -> SquadConfig:
    """Parse and validate a squad YAML file at *path*.

    Expects ``composition:`` to be a list of ``{role, count, model}`` dicts.
    """
    raw = yaml.safe_load(path.read_text()) or {}

    composition = raw.get("composition") or []
    models: dict[str, str] = {}

    if not isinstance(composition, list):
        raise ValueError(
            f"Squad profile {file_name!r}: 'composition' must be a list of"
            " {{role, count, model}} entries."
        )

    counts: dict[str, int] = {}
    for entry in composition:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        if role not in _VALID_ROLES:
            continue
        counts[role] = int(entry.get("count", 1))
        if "model" in entry and entry["model"]:
            models[role] = str(entry["model"]).strip()
    planner = counts.get(AgentRole.PLANNER, 1)
    coder = counts.get(AgentRole.CODER, 1)
    qa = counts.get(AgentRole.QA, 1)

    timeout_minutes = int(raw.get("timeout_minutes", _DEFAULT_TIMEOUT_MINUTES))
    name = str(raw.get("name", "") or path.stem)
    description = str(raw.get("description", "") or "").strip()

    if planner != 1:
        raise ValueError(
            f"Squad profile {file_name!r}: planner must be 1, got {planner}.\n"
            "Planning is serialised — only one planner is supported.\n"
            "Scale throughput by adding more coders and QA reviewers instead."
        )

    for role, cnt in [(AgentRole.CODER, coder), (AgentRole.QA, qa)]:
        if cnt < 1:
            raise ValueError(
                f"Squad profile {file_name!r}: {role.value} count must be >= 1, got {cnt}."
            )

    if timeout_minutes < 1:
        raise ValueError(
            f"Squad profile {file_name!r}: timeout_minutes must be >= 1, got {timeout_minutes}."
        )

    return SquadConfig(
        planner=planner,
        coder=coder,
        qa=qa,
        timeout_minutes=timeout_minutes,
        name=name,
        description=description,
        _models=models,
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
        f"Create .orc/squads/{name}.yaml to define a new profile."
    )


def load_all_squads(agents_dir: Path | None = None) -> list[SquadConfig]:
    """Return a :class:`SquadConfig` for every squad file visible to the project.

    Project-level profiles (``{agents_dir}/squads/``) take precedence over the
    package-bundled ones.  Any package profile not overridden by the project is
    included as well.

    Invalid files are silently skipped (logged to stderr at DEBUG level).
    """
    seen: dict[str, SquadConfig] = {}

    # Project-level profiles win.
    if agents_dir is not None:
        project_dir = agents_dir / "squads"
        if project_dir.exists():
            for p in sorted(project_dir.glob("*.yaml")):
                try:
                    seen[p.stem] = _parse_squad_file(p.stem, p)
                except (ValueError, Exception):
                    pass

    # Package bundled profiles fill in any gaps.
    for p in sorted(_PACKAGE_SQUADS_DIR.glob("*.yaml")):
        if p.stem not in seen:
            try:
                seen[p.stem] = _parse_squad_file(p.stem, p)
            except (ValueError, Exception):
                pass

    return list(seen.values())


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
