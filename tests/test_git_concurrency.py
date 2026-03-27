from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

from orc.mcp import tools as _tools


def test_run_git_concurrent_cwds(tmp_path: Path):
    """Simulate concurrent calls to _run_git with different CWDs."""
    dir1 = tmp_path / "repo1"
    dir2 = tmp_path / "repo2"
    dir1.mkdir()
    dir2.mkdir()

    call_info = []

    def mock_run(*args, **kwargs):
        # Capture the CWD for each call
        cwd = kwargs.get("cwd")
        call_info.append(cwd)
        return MagicMock(returncode=0)

    def git_caller(cwd: Path):
        _tools._run_git("status", cwd=cwd)

    with patch("subprocess.run", side_effect=mock_run):
        thread1 = threading.Thread(target=git_caller, args=(dir1,))
        thread2 = threading.Thread(target=git_caller, args=(dir2,))
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

    # The order of calls is not guaranteed, so we check that both CWDs were used
    assert len(call_info) == 2
    assert dir1 in call_info
    assert dir2 in call_info
