"""orc entry point and backwards-compatibility shim.

All public symbols are re-exported from their new homes.
"""

from __future__ import annotations

import subprocess  # noqa: F401 — tests access m.subprocess.run

from orc.board import (  # noqa: F401
    _active_task_name,
    _dev_board_file,
    _read_board,
    _read_work,
    _write_board,
    assign_task,
    clear_all_assignments,
    get_open_tasks,
    has_open_work,
    unassign_task,
)
from orc.cli import (
    _check_env_or_exit,  # noqa: F401
    app,  # noqa: F401
)
from orc.cli.bootstrap import _bootstrap, _copy_file, _tree, _write_file  # noqa: F401
from orc.cli.merge import _merge, _rebase_dev_on_main  # noqa: F401
from orc.cli.run import _run  # noqa: F401
from orc.cli.squads import _squads  # noqa: F401
from orc.cli.status import _dev_ahead_of_main, _dev_log_since_main, _status  # noqa: F401
from orc.cli.version import _version  # noqa: F401
from orc.config import (  # noqa: F401
    _PACKAGE_DIR,
    _PACKAGE_ROLES_DIR,
    _PLACEHOLDERS,
    _TEMPLATES_DIR,
    AGENTS_DIR,
    BOARD_FILE,
    DEV_WORKTREE,
    ENV_FILE,
    REPO_ROOT,
    ROLES_DIR,
    WORK_DEV_BRANCH,
    WORK_DIR,
    _find_config_dir,
    _init_paths,
    _load_placeholders,
    validate_env,
)
from orc.context import (  # noqa: F401
    _BLOCKED_TIMEOUT,
    _DEFAULT_MODEL,
    _boot_message_body,
    _parse_role_file,
    _read_adrs,
    _role_symbol,
    build_agent_context,
    invoke_agent,
    wait_for_human_reply,
)
from orc.git import (  # noqa: F401
    _CLOSE_BOARD,
    _QA_PASSED,
    _close_task_on_board,
    _complete_merge,
    _conflict_status,
    _derive_state_from_git,
    _derive_task_state,
    _ensure_dev_worktree,
    _ensure_feature_worktree,
    _feature_branch,
    _feature_branch_exists,
    _feature_has_commits_ahead_of_main,
    _feature_merged_into_dev,
    _feature_worktree_path,
    _last_feature_commit_message,
    _merge_feature_into_dev,
    _rebase_in_progress,
)
from orc.workflow import (  # noqa: F401
    _ORC_RESOLVED_RE,
    KNOWN_AGENTS,
    _do_close_board,
    _has_unresolved_block,
    _make_context_builder,
    _post_boot_message,
    _post_resolved,
    determine_next_agent,
)

if __name__ == "__main__":  # pragma: no cover
    app()
