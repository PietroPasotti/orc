"""Squad profile loader for the orc orchestrator.

A squad profile defines how many agents of each role may run in parallel,
which AI model each role should use, and what tool permissions agents have.
Profiles are YAML files stored in ``.orc/squads/`` (project-level) or in the
package's bundled ``squads/`` directory.

Usage::

    from orc.squad import load_squad, load_all_squads, SquadConfig

    squad = load_squad("default")                      # package default
    squad = load_squad("broad", orc_dir=orc_dir)   # project-level first
    squad.count("coder")                               # → 1
    squad.model("coder")                               # → "claude-sonnet-4.6"
    squad.permissions("coder")                         # → PermissionConfig(...)

    all_squads = load_all_squads(orc_dir=orc_dir)   # list[SquadConfig]

Profile format::

    name: default
    description: |
      One agent of each type, running sequentially.

    # Optional squad-level permission defaults (applied to all roles).
    permissions:
      mode: confined          # "confined" (default) or "yolo"
      allow_tools:            # extra tools beyond orc defaults
        - "shell(just:*)"
      deny_tools:             # tools explicitly denied
        - "shell(git push:*)"

    composition:
      - role: planner
        count: 1
        model: claude-sonnet-4.6
      - role: coder
        count: 4
        model: claude-sonnet-4.6
        permissions:          # role-level overrides, merged with squad defaults
          allow_tools:
            - "shell(npm:*)"
      - role: qa
        count: 2
        model: claude-sonnet-4.6
    timeout_minutes: 120  # watchdog: kill stuck agents after this many minutes

Permission resolution
---------------------
1. Orc defaults (hardcoded, always present when mode is "confined"):
   ``orc``, ``read``, ``write``, ``shell(git:*)``, plus worktree directory.
2. Squad-level ``permissions`` block merged on top.
3. Per-role ``permissions`` block merged on top of that.
4. If any level sets ``mode: yolo``, all allow/deny lists are ignored and the
   agent runs with full permissions (``--yolo`` / ``--allow-all``).

Constraints
-----------
* ``planner`` must be exactly **1**.  Scaling planners would require branch
  isolation of the dev worktree; that complexity is intentionally deferred.
  The CLI raises an error if ``planner != 1`` so the user gets a clear message
  rather than undefined behaviour.
* All counts must be ``>= 1``.
* ``timeout_minutes`` must be ``>= 1``.
* ``permissions.mode`` must be ``"confined"`` or ``"yolo"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Permission configuration
# ---------------------------------------------------------------------------

_VALID_MODES = frozenset({"confined", "yolo"})

# Orc's built-in defaults applied to every agent in confined mode.
# These are the minimum set of tools needed to do development work.
_ORC_DEFAULT_ALLOW_TOOLS: tuple[str, ...] = (
    "orc",  # all orc MCP board tools
    "read",  # read files
    "write",  # write/edit files
    "shell(git:*)",  # git operations
)


@dataclass(frozen=True)
class PermissionConfig:
    """Resolved permission configuration for one agent role.

    Attributes
    ----------
    mode:
        ``"confined"`` (default) — only explicitly allowed tools run without
        confirmation.  ``"yolo"`` — all tools are allowed (no restrictions).
    allow_tools:
        Ordered list of tool patterns that agents may use without prompting.
        Only meaningful when ``mode == "confined"``.
    deny_tools:
        Ordered list of tool patterns that are explicitly denied.
        Takes precedence over ``allow_tools``.  Only meaningful when
        ``mode == "confined"``.
    """

    mode: str = "confined"
    allow_tools: tuple[str, ...] = field(default_factory=tuple)
    deny_tools: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"permissions.mode must be 'confined' or 'yolo', got {self.mode!r}.")

    @property
    def is_yolo(self) -> bool:
        """Return ``True`` when the agent should run with unrestricted permissions."""
        return self.mode == "yolo"


def _merge_permissions(base: PermissionConfig, override: PermissionConfig) -> PermissionConfig:
    """Return a new :class:`PermissionConfig` with *override* merged onto *base*.

    ``mode: yolo`` in either level wins.  Allow/deny lists are concatenated
    (override appended to base, deduplicating while preserving order).
    """
    if base.is_yolo or override.is_yolo:
        return PermissionConfig(mode="yolo")

    seen_allow: dict[str, None] = dict.fromkeys(base.allow_tools)
    seen_allow.update(dict.fromkeys(override.allow_tools))
    seen_deny: dict[str, None] = dict.fromkeys(base.deny_tools)
    seen_deny.update(dict.fromkeys(override.deny_tools))
    return PermissionConfig(
        mode="confined",
        allow_tools=tuple(seen_allow),
        deny_tools=tuple(seen_deny),
    )


def _parse_permission_block(raw: object, context: str) -> PermissionConfig:
    """Parse a raw ``permissions:`` YAML value into a :class:`PermissionConfig`.

    Parameters
    ----------
    raw:
        The parsed YAML value (dict or None).
    context:
        Description for error messages (e.g. ``"squad 'default'", "role 'coder'``).
    """
    if not raw:
        return PermissionConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"permissions block for {context} must be a mapping, got {type(raw)}.")

    mode = str(raw.get("mode", "confined")).strip()
    if mode not in _VALID_MODES:
        raise ValueError(
            f"permissions.mode for {context} must be 'confined' or 'yolo', got {mode!r}."
        )

    def _to_strs(val: object, key: str) -> tuple[str, ...]:
        if val is None:
            return ()
        if not isinstance(val, list):
            raise ValueError(
                f"permissions.{key} for {context} must be a list of strings, got {type(val)}."
            )
        return tuple(str(v) for v in val)

    return PermissionConfig(
        mode=mode,
        allow_tools=_to_strs(raw.get("allow_tools"), "allow_tools"),
        deny_tools=_to_strs(raw.get("deny_tools"), "deny_tools"),
    )


class ReviewThreshold(StrEnum):
    """Severity level at which QA agents should reject work.

    QA agents will fail a review (send the task back to coders) when they
    encounter issues **at or above** the configured threshold:

    * ``CRITICAL`` — only reject on critical failures.
    * ``HIGH`` — reject on high-severity issues and above.
    * ``MID`` — reject on medium-severity issues and above.
    * ``LOW`` — reject on any issue, including low-severity ones (strictest).
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MID = "MID"
    LOW = "LOW"


_VALID_REVIEW_THRESHOLDS = frozenset(ReviewThreshold)
_DEFAULT_REVIEW_THRESHOLD = ReviewThreshold.LOW


class AgentRole(StrEnum):
    """Valid agent roles in an orc squad.

    The enum inherits from :class:`~enum.StrEnum` so values compare and format
    as their plain-string equivalents (e.g. ``AgentRole.CODER == "coder"`` is
    ``True`` and ``f"{AgentRole.CODER}"`` yields ``"coder"``), preserving
    backward compatibility with YAML config, external systems, and existing code
    that passes bare strings.
    """

    PLANNER = "planner"
    CODER = "coder"
    QA = "qa"


# Package-bundled squads directory (fallback when project-level squad not found).
# Lives inside the default template so there is a single source of truth.
_PACKAGE_SQUADS_DIR = Path(__file__).parent.parent / "templates" / "default" / "orc_cfg" / "squads"

_VALID_ROLES: frozenset[AgentRole] = frozenset(AgentRole)
_DEFAULT_TIMEOUT_MINUTES = 120


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
    _permissions: PermissionConfig = field(default_factory=PermissionConfig, compare=False)
    _role_permissions: dict[str, PermissionConfig] = field(default_factory=dict, compare=False)
    _review_threshold: ReviewThreshold = field(default=_DEFAULT_REVIEW_THRESHOLD, compare=False)

    @property
    def review_threshold(self) -> ReviewThreshold:
        """Return the configured QA review threshold.

        QA agents should reject work when they encounter issues **at or above**
        this severity level.  Defaults to ``LOW`` (reject on any issue).
        """
        return self._review_threshold

    def count(self, role: AgentRole | str) -> int:
        """Return the configured agent count for *role*."""
        if role not in _VALID_ROLES:
            raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(_VALID_ROLES)}")
        role_value = role.value if isinstance(role, AgentRole) else role
        return int(getattr(self, role_value))

    def model(self, role: AgentRole | str) -> str:
        """Return the configured model name for *role*.

        Falls back to the ``default-model`` setting from ``OrcConfig`` when no
        model is specified for the role.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(_VALID_ROLES)}")
        role_value = role.value if isinstance(role, AgentRole) else role
        if role_value in self._models:
            return self._models[role_value]
        import orc.config as _cfg  # noqa: PLC0415

        return _cfg.get().default_model

    def permissions(self, role: AgentRole | str) -> PermissionConfig:
        """Return the resolved :class:`PermissionConfig` for *role*.

        Resolution order:

        1. Orc built-in defaults (``_ORC_DEFAULT_ALLOW_TOOLS``).
        2. Squad-level ``permissions:`` block merged on top.
        3. Per-role ``permissions:`` merged on top.

        If any level declares ``mode: yolo`` the result is always yolo.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(_VALID_ROLES)}")
        role_value = role.value if isinstance(role, AgentRole) else role

        orc_defaults = PermissionConfig(
            mode="confined",
            allow_tools=_ORC_DEFAULT_ALLOW_TOOLS,
        )
        with_squad = _merge_permissions(orc_defaults, self._permissions)
        role_override = self._role_permissions.get(role_value, PermissionConfig())
        return _merge_permissions(with_squad, role_override)


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

    # Squad-level permissions (applies to all roles unless overridden).
    squad_permissions = _parse_permission_block(raw.get("permissions"), f"squad {file_name!r}")

    counts: dict[str, int] = {}
    role_permissions: dict[str, PermissionConfig] = {}
    review_threshold: ReviewThreshold = _DEFAULT_REVIEW_THRESHOLD
    for entry in composition:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        if role not in _VALID_ROLES:
            continue
        counts[role] = int(entry.get("count", 1))
        if "model" in entry and entry["model"]:
            models[role] = str(entry["model"]).strip()
        if "permissions" in entry:
            role_permissions[role] = _parse_permission_block(
                entry["permissions"], f"role {role!r} in squad {file_name!r}"
            )
        if role == AgentRole.QA and "review-threshold" in entry:
            raw_threshold = str(entry["review-threshold"]).strip().upper()
            try:
                review_threshold = ReviewThreshold(raw_threshold)
            except ValueError:
                raise ValueError(
                    f"Squad profile {file_name!r}: review-threshold for qa must be one of"
                    f" {', '.join(t.value for t in ReviewThreshold)}, got {raw_threshold!r}."
                )
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
        _permissions=squad_permissions,
        _role_permissions=role_permissions,
        _review_threshold=review_threshold,
    )


def load_squad(name: str, orc_dir: Path | None = None) -> SquadConfig:
    """Load and validate the squad profile *name*.

    Resolution order:
    1. ``{orc_dir}/squads/{name}.yaml`` if *orc_dir* is provided.
    2. Package bundled ``squads/{name}.yaml``.

    Raises:
        FileNotFoundError: Profile file not found in either location.
        ValueError: Profile contains invalid values (e.g. ``planner != 1``).
    """
    # Check project-level squads directory first.
    if orc_dir is not None:
        project_path = orc_dir / "squads" / f"{name}.yaml"
        if project_path.exists():
            return _parse_squad_file(name, project_path)

    # Fall back to package bundled squads.
    package_path = _PACKAGE_SQUADS_DIR / f"{name}.yaml"
    if package_path.exists():
        return _parse_squad_file(name, package_path)

    available = _list_available(orc_dir)
    raise FileNotFoundError(
        f"Squad profile {name!r} not found.\n"
        f"Available profiles: {available}\n"
        f"Create .orc/squads/{name}.yaml to define a new profile."
    )


def load_all_squads(orc_dir: Path | None = None) -> list[SquadConfig]:
    """Return a :class:`SquadConfig` for every squad file visible to the project.

    Project-level profiles (``{orc_dir}/squads/``) take precedence over the
    package-bundled ones.  Any package profile not overridden by the project is
    included as well.

    Invalid files are silently skipped (logged to stderr at DEBUG level).
    """
    seen: dict[str, SquadConfig] = {}

    # Project-level profiles win.
    if orc_dir is not None:
        project_dir = orc_dir / "squads"
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


def list_squads(orc_dir: Path | None = None) -> list[str]:
    """Return sorted list of available squad profile names.

    If *orc_dir* is provided, returns profiles from ``orc_dir/squads/``.
    Otherwise returns profiles from the package bundled squads directory.
    """
    if orc_dir is not None:
        squads_dir = orc_dir / "squads"
    else:
        squads_dir = _PACKAGE_SQUADS_DIR

    if not squads_dir.exists():
        return []
    return sorted(p.stem for p in squads_dir.glob("*.yaml"))


def _list_available(orc_dir: Path | None = None) -> str:
    names = list_squads(orc_dir)
    if not names and orc_dir is not None:
        # Also check package squads for the error message
        names = list_squads()
    return ", ".join(names) if names else "(none)"
