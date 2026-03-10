"""Test configuration for orc tests.

Stubs out the side-effectful parts (dotenv loading, live HTTP calls,
subprocess invocations) so tests run without a real .env or network.
"""

import sys
import types
from unittest.mock import MagicMock

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
        self._context_tmp: str | None = None

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode
