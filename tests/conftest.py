"""Test configuration for orc tests.

Stubs out the side-effectful parts (dotenv loading, live HTTP calls,
subprocess invocations) so tests run without a real .env or network.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

from orc.messaging.messages import ChatMessage

# ---------------------------------------------------------------------------
# Stub out dotenv before any orc module is imported
# ---------------------------------------------------------------------------

dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", dotenv_stub)


# ---------------------------------------------------------------------------
# Stub out httpx so telegram.py never makes real network calls
# ---------------------------------------------------------------------------

httpx_stub = types.ModuleType("httpx")
httpx_stub.Client = MagicMock
httpx_stub.HTTPStatusError = Exception
sys.modules.setdefault("httpx", httpx_stub)


# ---------------------------------------------------------------------------
# Ensure orc.config is always initialised for tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _init_config(tmp_path, monkeypatch):
    """Initialise orc.config with a temporary .orc/ directory for every test.

    After the test, the singleton is reset so each test starts clean.
    The ``init`` function is patched to a no-op so the CLI callback
    (which always calls ``init()``) doesn't override the test's Config.
    Tests that need real ``init()`` can call ``_real_init(...)`` via the
    yielded value.
    """
    import orc.config as _cfg

    orc_dir = tmp_path / ".orc"
    orc_dir.mkdir(exist_ok=True)
    (orc_dir / "work").mkdir(exist_ok=True)
    _real_init = _cfg.init
    _real_init(orc_dir, repo_root=tmp_path)
    monkeypatch.setattr(_cfg, "init", lambda *a, **kw: _cfg.get())
    yield _real_init
    monkeypatch.setattr(_cfg, "_config", None)


# ---------------------------------------------------------------------------
# Helpers used across tests
# ---------------------------------------------------------------------------


def make_msg(text: str, ts: int = 1_700_000_000, username: str = "bot") -> ChatMessage:
    """Build a minimal chat message for tests."""
    return ChatMessage(text=text, date=ts, sender_name=username)


class FakePopen:
    """Minimal subprocess.Popen stand-in that reports immediate success."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


# ---------------------------------------------------------------------------
# Shared service fakes (used by test_dispatcher, test_run, test_integration)
# ---------------------------------------------------------------------------


class FakeBoard:
    """Mutable fake for BoardService — override attributes freely in tests."""

    def __init__(
        self,
        *,
        get_tasks=None,
        get_pending_visions=None,
        get_pending_reviews=None,
        scan_todos=None,
        get_blocked_tasks=None,
    ):
        self.get_tasks = get_tasks or (lambda: [])
        self.assign_task = lambda task, agent: None
        self.unassign_task = lambda task: None
        self.delete_task = lambda task: None
        self.get_pending_visions = get_pending_visions or (lambda: ["placeholder.md"])
        self.get_pending_reviews = get_pending_reviews or (lambda: [])
        self.scan_todos = scan_todos or (lambda: [])
        self.get_blocked_tasks = get_blocked_tasks or (lambda: [])

    def is_empty(self) -> bool:
        return not (
            self.get_tasks()
            or self.get_pending_visions()
            or self.scan_todos()
            or self.get_pending_reviews()
            or self.get_blocked_tasks()
        )

    def query_tasks(self, status: str) -> list[str]:
        return [t.name for t in self.get_tasks() if t.status == status]


class FakeWorktree:
    """Mutable fake for WorktreeService."""

    def __init__(self, tmp_path):
        self.ensure_dev_worktree = lambda: tmp_path
        self.ensure_feature_worktree = lambda t: tmp_path


class FakeMessaging:
    """Mutable fake for MessagingService — override attributes freely in tests."""

    def __init__(self, *, get_messages=None):
        self.get_messages = get_messages or (lambda: [])
        self.post_boot_message = lambda agent_id, body: None


class FakeWorkflow:
    """Mutable fake for WorkflowService."""

    def __init__(self, *, derive_task_state=None):
        self.derive_task_state = derive_task_state or (lambda t, td=None: ("coder", "ready"))
        self.merge_feature = lambda task: None


class FakeAgent:
    """Mutable fake for AgentService."""

    def __init__(self, tmp_path, *, spawn_fn=None):
        from orc.ai.backends import SpawnResult

        def _default_spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        self.build_context = lambda role, agent_id, msgs, wt: ("model", "ctx")
        self.spawn = spawn_fn or _default_spawn
        self.boot_message_body = lambda agent_id: f"working on something ({agent_id})"


# ---------------------------------------------------------------------------
# Fixture: silence common side-effect patches used in many tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_git(monkeypatch, tmp_path):
    """Patch the five most-mocked git helpers to no-ops.

    Parameterise individually if a test needs non-default behaviour, e.g.:
        def test_something(mock_git, monkeypatch):
            monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: True)
    """
    import orc.git.core as _git

    monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
    monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
    monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
    monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
    monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def mock_telegram(monkeypatch):
    """Patch Telegram helpers so no network calls occur and messages are captured."""
    import orc.messaging.telegram as tg

    sent: list[str] = []
    monkeypatch.setattr(tg, "get_messages", lambda: [])
    monkeypatch.setattr(tg, "send_message", lambda text: sent.append(text))
    monkeypatch.setattr(tg, "is_configured", lambda: False)
    return sent


@pytest.fixture()
def mock_spawn(monkeypatch):
    """Patch inv.spawn to return a FakePopen immediately."""
    import orc.ai.invoke as inv
    from orc.ai.backends import SpawnResult

    monkeypatch.setattr(
        inv,
        "spawn",
        lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
    )


@pytest.fixture()
def board_file(tmp_path):
    """Create a minimal board.yaml and return a helper for writing content.

    Usage::

        def test_something(board_file):
            board_file("counter: 1\\nopen:\\n  - name: 0001-foo.md\\n")
    """
    board = tmp_path / ".orc" / "work" / "board.yaml"
    board.parent.mkdir(parents=True, exist_ok=True)

    def _write(content: str) -> None:
        board.write_text(content)

    return _write


@pytest.fixture()
def mock_validate_env(monkeypatch):
    """Suppress config.validate_env so tests don't need real env vars."""
    import orc.config as _cfg

    monkeypatch.setattr(_cfg, "validate_env", lambda: [])


@pytest.fixture()
def mock_rebase(monkeypatch):
    """Suppress _rebase_dev_on_main so tests don't hit subprocess."""
    import orc.git.core as _git_mod

    monkeypatch.setattr(_git_mod, "_rebase_dev_on_main", lambda *_: None)


# ---------------------------------------------------------------------------
# Helper functions for dispatcher tests (used across test_dispatcher_*.py)
# ---------------------------------------------------------------------------


def make_agent(tmp_path, *, role: str = "coder", task: str = "0001-foo.md"):
    """Construct a minimal AgentProcess for testing."""
    from orc.engine.pool import AgentProcess

    return AgentProcess(
        agent_id=f"{role}-1",
        role=role,
        model="copilot",
        task_name=task,
        process=FakePopen(),
        worktree=tmp_path,
        log_path=tmp_path / f"{role}.log",
        log_fh=None,
        context_tmp=None,
    )


def minimal_squad(**kw):
    """Construct a minimal SquadConfig for testing."""
    from orc.squad import SquadConfig

    defaults = dict(
        planner=1,
        coder=1,
        qa=1,
        timeout_minutes=60,
        name="test",
        description="",
        _models={},
    )
    defaults.update(kw)
    return SquadConfig(**defaults)


def make_services(
    tmp_path,
    *,
    get_messages=None,
    get_tasks=None,
    derive_task_state=None,
    spawn_fn=None,
    get_pending_visions=None,
    get_pending_reviews=None,
    scan_todos=None,
    get_blocked_tasks=None,
):
    """Return a SimpleNamespace of fully-wired fake services for Dispatcher tests."""
    import types

    board_dir = tmp_path / ".orc" / "work"
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "board.yaml").write_text("counter: 0\ntasks: []\n")

    return types.SimpleNamespace(
        board=FakeBoard(
            get_tasks=get_tasks,
            get_pending_visions=get_pending_visions,
            get_pending_reviews=get_pending_reviews,
            scan_todos=scan_todos,
            get_blocked_tasks=get_blocked_tasks,
        ),
        worktree=FakeWorktree(tmp_path),
        messaging=FakeMessaging(
            get_messages=get_messages,
        ),
        workflow=FakeWorkflow(derive_task_state=derive_task_state),
        agent=FakeAgent(tmp_path, spawn_fn=spawn_fn),
    )


def make_dispatcher(squad, svcs, *, dry_run: bool = False, only_role=None, hooks=None):
    """Convenience wrapper: construct a Dispatcher from a services namespace."""
    from orc.engine.dispatcher import Dispatcher

    return Dispatcher(
        squad,
        board=svcs.board,
        worktree=svcs.worktree,
        messaging=svcs.messaging,
        workflow=svcs.workflow,
        agent=svcs.agent,
        hooks=hooks,
        dry_run=dry_run,
        only_role=only_role,
    )


def setup_work(d):
    """No-op: the dispatcher reads board state directly on each cycle."""
    pass
