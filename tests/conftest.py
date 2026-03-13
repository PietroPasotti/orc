"""Test configuration for orc tests.

Stubs out the side-effectful parts (dotenv loading, live HTTP calls,
subprocess invocations) so tests run without a real .env or network.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

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


def make_msg(text: str, ts: int = 1_700_000_000, username: str = "bot") -> dict:
    """Build a minimal Telegram message dict."""
    return {
        "text": text,
        "date": ts,
        "from": {"username": username, "first_name": username},
    }


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
