"""Git conflict resolution via agent delegation.

Extracts the conflict-resolution logic that was previously duplicated between
:mod:`orc.workflow` (merge conflicts) and :mod:`orc.cli.merge` (rebase
conflicts) into a single, testable :class:`ConflictResolver`.

Both scenarios follow the same pattern:
1. A git operation (merge or rebase) stops with conflicts.
2. A coder agent is invoked with a detailed task description.
3. If the agent succeeds the operation is assumed complete.
4. If the agent fails or the git state is still "in progress", a
   :class:`~typer.Exit` is raised so the caller can abort cleanly.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

import orc.config as _cfg
import orc.engine.context as _ctx
import orc.git.core as _git
from orc.squad import SquadConfig

logger = structlog.get_logger(__name__)


class ConflictResolver:
    """Resolves git merge and rebase conflicts by delegating to a coder agent.

    Parameters
    ----------
    squad_cfg:
        Squad configuration; used to determine the coder model.
    messages:
        Current Telegram message history; passed as context to the agent.
    """

    def __init__(self, squad_cfg: SquadConfig, messages: list[dict]) -> None:
        self.squad_cfg = squad_cfg
        self.messages = messages

    def _coder_model(self) -> str:
        return self.squad_cfg.model("coder")

    def resolve_merge_conflict(self, branch: str, worktree: Path, status_output: str) -> None:
        """Resolve a paused ``git merge --no-ff`` conflict via a coder agent.

        The dev worktree is left in mid-merge state by the caller before this
        method is called.  The coder is instructed to:

        1. Resolve conflict markers.
        2. Stage the resolved files.
        3. Run ``git merge --continue``.

        Parameters
        ----------
        branch:
            The feature branch that caused the conflict.
        worktree:
            The dev worktree where the merge is paused.
        status_output:
            Output of ``git status --short`` showing conflicting files.

        Raises
        ------
        typer.Exit
            If the coder agent exits non-zero or the merge is still in
            progress after the agent exits.
        """
        typer.echo(f"⚠ Merge conflict on {branch!r}:\n{status_output}\nDelegating to coder agent…")

        conflict_extra = (
            f"## Feature merge conflict — your task\n\n"
            f"A `git merge --no-ff {branch}` into `{_cfg.WORK_DEV_BRANCH}` was attempted "
            f"and stopped with conflicts.  The merge is currently paused in the dev "
            f"worktree at `{worktree}`.\n\n"
            f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
            "**What you must do:**\n"
            "1. Open each conflicting file, resolve the conflict markers "
            "(`<<<<<<<`, `=======`, `>>>>>>>`).\n"
            "2. `git add <resolved-file>` for each resolved file.\n"
            "3. `git merge --continue` to complete the merge.\n"
            "4. Do NOT `git merge --abort`. Finish the merge.\n"
            "5. Exit when the merge is complete.\n"
        )

        model, context = _ctx.build_agent_context(
            "coder",
            self.messages,
            extra=conflict_extra,
            worktree=worktree,
            model=self._coder_model(),
        )
        rc = _ctx.invoke_agent("coder", context, model)

        if rc != 0:
            logger.error("coder agent failed to resolve merge conflict", exit_code=rc)
            typer.echo(f"✗ Coder agent exited with code {rc} while resolving merge conflict.")
            raise typer.Exit(code=rc)

        if _git._merge_in_progress(worktree):
            logger.error("merge still in progress after coder exited", branch=branch)
            typer.echo("✗ Merge still in progress after agent exit.  Manual intervention needed.")
            raise typer.Exit(code=1)

        logger.info("merge conflict resolved by coder agent", branch=branch)
        typer.echo(f"✓ Merge conflict on {branch!r} resolved by coder agent.")

    def resolve_rebase_conflict(self, worktree: Path, status_output: str) -> None:
        """Resolve a paused ``git rebase`` conflict via a coder agent.

        The dev worktree is left in mid-rebase state by the caller before this
        method is called.  The coder is instructed to:

        1. Resolve conflict markers.
        2. Stage the resolved files.
        3. Run ``git rebase --continue``.

        Parameters
        ----------
        worktree:
            The dev worktree where the rebase is paused.
        status_output:
            Output of ``git status --short`` showing conflicting files.

        Raises
        ------
        typer.Exit
            If the coder agent exits non-zero or the rebase is still in
            progress after the agent exits.
        """
        typer.echo(f"⚠ Startup rebase conflict:\n{status_output}\nDelegating to coder agent…")

        conflict_extra = (
            "## Startup rebase conflict — your task\n\n"
            f"A `git rebase main` of the `{_cfg.WORK_DEV_BRANCH}` branch was attempted at "
            "session start and stopped with conflicts.  The rebase is currently paused in "
            "the dev worktree.\n\n"
            f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
            "**What you must do:**\n"
            "1. Open each conflicting file, resolve the conflict markers (`<<<<<<<`, "
            "`=======`, `>>>>>>>`).\n"
            "2. `git add <resolved-file>` for each resolved file.\n"
            "3. `git rebase --continue` (repeat steps 1–3 if git stops again).\n"
            "4. Do NOT `git rebase --abort`. Finish the rebase.\n"
            "5. Exit when the rebase is complete.\n"
        )

        model, context = _ctx.build_agent_context(
            "coder",
            self.messages,
            extra=conflict_extra,
            model=self._coder_model(),
        )
        rc = _ctx.invoke_agent("coder", context, model)

        if rc != 0:
            logger.error("coder agent failed to resolve startup rebase", exit_code=rc)
            typer.echo(f"✗ Coder agent exited with code {rc} while resolving startup rebase.")
            raise typer.Exit(code=rc)

        if _git._rebase_in_progress(worktree):
            logger.error("rebase still in progress after coder exited")
            typer.echo("✗ Rebase still in progress after agent exit. Manual intervention needed.")
            raise typer.Exit(code=1)

        logger.info("dev rebased on main after conflict resolution by coder")
        typer.echo("✓ dev rebased on main (conflicts resolved by coder).")
