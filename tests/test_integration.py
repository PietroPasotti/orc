"""Integration tests: full orc workflow loop.

Verifies the complete design → coding → QA → merge → design cycle with:

- A real temporary git repository bootstrapped via ``orc bootstrap``.
- A single dummy vision document added before the run.
- All LLM invocations replaced by scripted :class:`~conftest.FakePopen` handlers
  that perform the same git / board side-effects a real agent would.
- All Telegram HTTP calls replaced by a local in-process log.

Reusable fixtures
-----------------
``git_project``
    Creates a temp git repo, runs ``orc bootstrap``, adds a vision doc, and
    records an initial commit.  Returns the project root path.

``orc_env``
    Patches every ``main.py`` module-level path global to point at the temp
    project so git operations, board reads/writes, and role lookups all use
    the isolated tree.

``mock_telegram``
    Replaces ``tg.send_message`` with a local-log-only variant and stubs
    ``tg._get_telegram_updates`` to return an empty list.

``scripted_spawn``
    Patches ``inv.spawn`` with a deterministic four-step script:
    planner-1 → coder-1 → qa-1 → planner-2.  Each step performs the git /
    board side-effects the real agent would perform, then returns a
    :class:`~conftest.FakePopen` that completes immediately.
    Returns the list of recorded spawn calls for assertions.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace as _replace
from pathlib import Path

import pytest
import yaml
from conftest import FakePopen
from typer.testing import CliRunner

import orc.ai.invoke as inv
import orc.config as _cfg
import orc.engine.dispatcher as _disp
import orc.git.core as _git
import orc.main as m
import orc.messaging.telegram as tg
from orc.ai.backends import SpawnResult

runner = CliRunner()

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_TASK_NAME = "0001-feature-x.md"
_VISION_DOC = "# Feature X\n\nBuild feature X.\n"


# ---------------------------------------------------------------------------
# Fixture: real git project with orc bootstrapped
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_project(tmp_path, monkeypatch):
    """Create a temp git repo, bootstrap orc, and add a dummy vision document.

    Changes the working directory to *tmp_path* for the duration of the test
    so that any ``Path.cwd()``-relative code (e.g. ``bootstrap``) lands in
    the right place.

    Returns the project root :class:`~pathlib.Path`.
    """
    monkeypatch.chdir(tmp_path)

    # Minimal git setup
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "orc-test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Orc Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    (tmp_path / "README.md").write_text("# Test Project\n")

    # Bootstrap the orc/ structure using the real CLI
    result = runner.invoke(m.app, ["bootstrap"], input="\n\n", catch_exceptions=False)
    assert result.exit_code == 0, f"bootstrap failed:\n{result.output}"

    # Add a single dummy vision document (goes in .orc/vision/)
    (tmp_path / ".orc" / "vision" / "feature-x.md").write_text(_VISION_DOC)

    # Initial commit — required for git worktree operations
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Fixture: patch main.py path globals to the temp project
# ---------------------------------------------------------------------------


@pytest.fixture()
def orc_env(git_project, monkeypatch):
    """Redirect every ``main.py`` path global to the temp project tree.

    Returns the project root :class:`~pathlib.Path`.
    """
    root = git_project
    orc_dir = root / ".orc"
    dev_wt = root.parent / f"{root.name}-dev"

    monkeypatch.setattr(
        _cfg,
        "_config",
        _replace(
            _cfg.get(),
            repo_root=root,
            orc_dir=orc_dir,
            work_dir=orc_dir / "work",
            vision_dir=orc_dir / "vision",
            roles_dir=orc_dir / "roles",
            env_file=root / ".env",
            dev_worktree=dev_wt,
        ),
    )

    return root


# ---------------------------------------------------------------------------
# Fixture: mock Telegram (local log only, no HTTP)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_telegram(orc_env, monkeypatch):
    """Stub out all Telegram HTTP calls.

    - ``tg.send_message`` writes only to the local chat.log (no HTTP request).
    - ``tg._get_telegram_updates`` always returns an empty list.
    - ``tg._LOG_FILE`` is redirected to the temp project's ``.orc/chat.log``
      so each test starts with a clean message history.

    Returns the path to the chat.log file.
    """
    log_file = orc_env / ".orc" / "chat.log"
    monkeypatch.setattr(tg, "_LOG_FILE", log_file)
    monkeypatch.setattr(tg, "_get_telegram_updates", lambda limit=100: [])

    def _send_local(text: str) -> dict:
        tg._append_to_log(text)
        return {}

    monkeypatch.setattr(tg, "send_message", _send_local)
    return log_file


# ---------------------------------------------------------------------------
# Scripted-spawn agent handlers
# ---------------------------------------------------------------------------


def _planner_handler(task_name: str):
    """Return a callable that simulates a planner creating one task on the board.

    The handler is invoked with the same ``(context, cwd, model, log_path)``
    signature as ``inv.spawn`` would pass to the real agent subprocess.
    *cwd* is the dev worktree.
    """

    def handler(context: str, cwd: Path, model: str | None, log_path: Path | None) -> None:
        import orc.config as _orc_cfg

        work_dir = _orc_cfg.get().work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        board_path = work_dir / "board.yaml"
        task_file = work_dir / task_name

        task_file.write_text(f"# {task_name}\n\nImplement feature X per vision doc.\n")

        board = yaml.safe_load(board_path.read_text()) if board_path.exists() else {}
        board = board or {}
        board.setdefault("open", [])
        board["open"].append({"name": task_name, "status": "planned"})
        board["counter"] = 2
        board_path.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))

        tg._append_to_log(
            tg.format_agent_message("planner-1", "ready", f"Created task {task_name}.")
        )

    return handler


def _coder_handler():
    """Return a callable that simulates a coder implementing a feature.

    *cwd* is the feature worktree (on the feature branch).
    """

    def handler(context: str, cwd: Path, model: str | None, log_path: Path | None) -> None:
        import orc.board as _board_mod

        impl = cwd / "feature_x.py"
        impl.write_text("# Feature X implementation\n\ndef feature_x():\n    pass\n")

        subprocess.run(["git", "add", "feature_x.py"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: implement feature-x"],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

        _board_mod.set_task_status(_TASK_NAME, "review")
        tg._append_to_log(tg.format_agent_message("coder-1", "done", "Implemented feature X."))

    return handler


def _qa_handler():
    """Return a callable that simulates a QA agent committing a passed verdict.

    *cwd* is the feature worktree.  Board status is updated to ``approved``
    so the orchestrator recognises the QA verdict and triggers a merge.
    """

    def handler(context: str, cwd: Path, model: str | None, log_path: Path | None) -> None:
        import orc.board as _board_mod

        qa_note = cwd / "qa_review.txt"
        qa_note.write_text("Reviewed. All checks passed.\n")

        subprocess.run(["git", "add", "qa_review.txt"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore(qa): feature-x reviewed, all tests pass"],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

        _board_mod.set_task_status(_TASK_NAME, "approved")
        tg._append_to_log(
            tg.format_agent_message("qa-1", "passed", "Review complete, all tests pass.")
        )

    return handler


def _idle_planner_handler():
    """Return a callable for a planner that finds nothing new to do (loop-back).

    This represents the workflow looping back to the design phase after the
    feature has been merged.  The planner simply signals readiness without
    creating any additional tasks.
    """

    def handler(context: str, cwd: Path, model: str | None, log_path: Path | None) -> None:
        tg._append_to_log(
            tg.format_agent_message("planner-2", "ready", "No new tasks at this time.")
        )

    return handler


# ---------------------------------------------------------------------------
# Fixture: scripted spawn (replaces inv.spawn with deterministic handlers)
# ---------------------------------------------------------------------------


@pytest.fixture()
def scripted_spawn(orc_env, mock_telegram, monkeypatch):
    """Replace ``inv.spawn`` with a deterministic four-step script.

    Script (in order):
      1. planner-1 — writes a task to the board and commits it to dev.
      2. coder-1   — creates a feature commit on the feature branch.
      3. qa-1      — commits a structured ``chore(qa-1.approve.0001):`` verdict
         on the feature branch.
      4. planner-2 — posts a ready message (loop-back to design, no new tasks).

    Each step returns a :class:`~conftest.FakePopen` that reports immediate
    success (``returncode=0``), so the dispatcher moves on without waiting.

    The ``_POLL_INTERVAL`` is also zeroed out so the test does not sleep.

    Returns a list of dicts recording each spawn call::

        [{"idx": 0, "cwd": Path(...), "model": "..."},  ...]
    """
    handlers = [
        _planner_handler(_TASK_NAME),
        _coder_handler(),
        _qa_handler(),
        _idle_planner_handler(),
    ]

    call_records: list[dict] = []
    idx_box = [0]  # mutable container so the closure can mutate it

    def _fake_spawn(
        context: str,
        cwd: Path,
        model: str | None = None,
        log_path: Path | None = None,
    ) -> SpawnResult:
        idx = idx_box[0]
        call_records.append({"idx": idx, "cwd": cwd, "model": model})
        if idx < len(handlers):
            handlers[idx](context, cwd, model, log_path)
        idx_box[0] += 1
        return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

    monkeypatch.setattr(inv, "spawn", _fake_spawn)
    monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

    return call_records


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBootstrap:
    """Verify that ``orc bootstrap`` produces the expected directory layout."""

    def test_creates_expected_structure(self, git_project):
        root = git_project
        orc = root / ".orc"
        assert (orc / "roles" / "planner" / "_main.md").exists()
        assert (orc / "roles" / "coder" / "_main.md").exists()
        assert (orc / "roles" / "qa" / "_main.md").exists()
        assert (orc / "squads" / "default.yaml").exists()
        assert (orc / "work" / "board.yaml").exists()
        assert (orc / "vision" / "feature-x.md").exists()
        assert (root / ".env.example").exists()

    def test_board_starts_empty(self, git_project):
        orc = git_project / ".orc"
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["open"] == []
        assert board["done"] == []
        assert board["counter"] == 1

    def test_vision_doc_content(self, git_project):
        orc = git_project / ".orc"
        content = (orc / "vision" / "feature-x.md").read_text()
        assert content == _VISION_DOC


class TestFullWorkflowLoop:
    """Verify the complete design → coding → QA → merge → design cycle."""

    def test_design_coding_qa_back_to_design(
        self,
        orc_env,
        mock_telegram,
        scripted_spawn,
        monkeypatch,
    ):
        """Run four agent invocations and assert the full workflow state machine."""
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_git, "_rebase_dev_on_main", lambda *_: None)

        result = runner.invoke(m.app, ["run", "--maxcalls", "4"], catch_exceptions=False)

        assert result.exit_code == 0, f"orc run exited non-zero:\n{result.output}"

        # ── Spawn count ──────────────────────────────────────────────────────
        assert len(scripted_spawn) == 4, (
            f"Expected 4 spawns (planner→coder→qa→planner), "
            f"got {len(scripted_spawn)}.\nCLI output:\n{result.output}"
        )

        # ── Telegram message sequence ────────────────────────────────────────
        messages = tg.get_messages()
        parsed: list[tuple[str, str]] = []
        for msg in messages:
            mo = tg._MSG_RE.match(msg.get("text", ""))
            if mo:
                parsed.append((mo.group(1), mo.group(2)))

        agent_ids = {name for name, _ in parsed}
        assert "planner-1" in agent_ids, f"planner-1 missing from messages: {parsed}"
        assert "coder-1" in agent_ids, f"coder-1 missing from messages: {parsed}"
        assert "qa-1" in agent_ids, f"qa-1 missing from messages: {parsed}"
        assert "planner-2" in agent_ids, f"planner-2 missing from messages: {parsed}"

        # Non-boot terminal states must all be present
        terminal = [(name, state) for name, state in parsed if state != "boot"]
        assert ("planner-1", "ready") in terminal, terminal
        assert ("coder-1", "done") in terminal, terminal
        assert ("qa-1", "passed") in terminal, terminal
        assert ("planner-2", "ready") in terminal, terminal

        # The first occurrence of each agent must follow the expected order
        def _first(target: str) -> int:
            return next(i for i, (n, _) in enumerate(parsed) if n == target)

        assert _first("planner-1") < _first("coder-1"), "planner must precede coder"
        assert _first("coder-1") < _first("qa-1"), "coder must precede qa"
        assert _first("qa-1") < _first("planner-2"), "qa must precede second planner"

        # ── Board state after merge ──────────────────────────────────────────
        orc_dir = orc_env / ".orc"
        board_path = orc_dir / "work" / "board.yaml"
        board = yaml.safe_load(board_path.read_text())

        assert board["open"] == [], "Open task list should be empty after the merge"

        done_names = [(t["name"] if isinstance(t, dict) else str(t)) for t in board.get("done", [])]
        assert _TASK_NAME in done_names, f"{_TASK_NAME!r} not found in done list: {done_names}"


class TestNoWorkExitsCleanly:
    """Verify that ``orc run`` exits 0 with an informational message when there
    is genuinely nothing to do: no vision docs, an empty board, and no open
    feature branches."""

    def test_exits_zero_with_info_message(
        self,
        orc_env,
        mock_telegram,
        monkeypatch,
    ):
        """Running ``orc run`` in a fully-idle project must exit 0 and print
        a human-readable message instead of spawning any agents."""
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_git, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        # Remove all vision docs so the project is genuinely idle: no unplanned
        # vision docs, empty board, no open branches.
        orc_dir = orc_env / ".orc"
        for f in (orc_dir / "vision").glob("*.md"):
            if f.name.lower() != "readme.md":
                f.unlink()

        # Guard: confirm the board really is empty before the run.
        board = yaml.safe_load((orc_dir / "work" / "board.yaml").read_text())
        assert board["open"] == []

        spawn_calls: list = []
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: spawn_calls.append(a) or (None, None))

        result = runner.invoke(m.app, ["run", "--maxcalls", "UNLIMITED"], catch_exceptions=False)

        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        assert "No pending work" in result.output, (
            f"Expected 'No pending work' in output:\n{result.output}"
        )
        assert spawn_calls == [], (
            f"Expected no agents to be spawned, but got {len(spawn_calls)} spawn call(s)"
        )
