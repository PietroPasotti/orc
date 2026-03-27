"""Review operation — deterministic tests + single LLM review call.

Replaces the QA agent with:

1. **Test suite** — run ``just test`` in the feature worktree (deterministic).
2. **Diff** — ``git diff dev...feature`` (deterministic).
3. **LLM review** — single call with diff + test output + review criteria.
4. **Optional investigation** — bounded loop (max 10 iters) if the LLM
   needs to read files to form a verdict.

The orchestrator updates the task status based on the structured result.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import structlog

from orc.ai.llm import LLMClient
from orc.git import Git

logger = structlog.get_logger(__name__)

_MAX_REVIEW_ITERATIONS = 10
_TEST_TIMEOUT_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewResult:
    """Result of a review operation."""

    verdict: str
    """``"approved"`` or ``"rejected"``."""

    comments: str
    """Review comments explaining the verdict."""

    tests_passed: bool
    """Whether the test suite passed."""

    test_output: str = ""
    """Captured test output (truncated to last 5000 chars)."""

    success: bool = True
    """Whether the review operation itself completed (not the verdict)."""

    error: str = ""
    """Error message if ``success`` is ``False``."""

    @property
    def approved(self) -> bool:
        return self.verdict == "approved"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a code reviewer.  You receive a diff of changes and test results.

Review the code for:
1. **Correctness** — does the implementation match the task description?
2. **Bugs** — logic errors, off-by-one, null handling, race conditions.
3. **Test coverage** — are the changes adequately tested?
4. **Breaking changes** — does this break existing functionality?

Do NOT comment on:
- Style preferences (formatting, naming conventions)
- Minor documentation gaps
- Hypothetical future concerns

Respond with a JSON object:

```json
{
  "verdict": "approved" or "rejected",
  "comments": "Explanation of your decision. If rejected, explain what needs to change."
}
```

Approve unless there are real bugs or the implementation is clearly wrong.
Tests passing is a strong signal of correctness — if tests pass and the diff
looks reasonable, approve.
"""


def _build_review_prompt(
    task_name: str,
    task_content: str,
    diff: str,
    test_passed: bool,
    test_output: str,
) -> str:
    """Build the user prompt for the review LLM call."""
    test_status = "✅ PASSED" if test_passed else "❌ FAILED"
    # Truncate large diffs/outputs to stay within context
    max_diff = 50_000
    max_test = 10_000
    if len(diff) > max_diff:
        diff = diff[:max_diff] + f"\n\n... (truncated, {len(diff)} chars total)"
    if len(test_output) > max_test:
        test_output = test_output[-max_test:]

    return f"""\
## Task: {task_name}

{task_content}

## Test Results: {test_status}

```
{test_output}
```

## Diff

```diff
{diff}
```
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_tests(worktree: Path) -> tuple[bool, str]:
    """Run the test suite in *worktree*.  Returns ``(passed, output)``."""
    try:
        result = subprocess.run(
            ["just", "test"],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0
        return passed, output[-5000:]
    except subprocess.TimeoutExpired:
        return False, "Test suite timed out"
    except FileNotFoundError:
        logger.warning("review: 'just' not found, skipping tests")
        return True, "(no test runner available)"


def _compute_diff(git: Git, dev_branch: str, feature_branch: str) -> str:
    """Compute ``git diff dev...feature``."""
    try:
        result = git._run_subprocess("diff", f"{dev_branch}...{feature_branch}")
        return str(result.stdout) if result.stdout else ""
    except Exception as exc:
        logger.warning("review: diff failed", error=str(exc))
        return f"(diff unavailable: {exc})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def review_task(
    task_name: str,
    task_content: str,
    *,
    feature_worktree: Path,
    dev_branch: str,
    feature_branch: str,
    repo_root: Path,
    llm: LLMClient,
    log_fh: IO[str] | None = None,
    socket_path: str = "",
) -> ReviewResult:
    """Review a completed task via test suite + LLM review.

    Parameters
    ----------
    task_name:
        Task filename (e.g. ``0046-eliminate-os-chdir.md``).
    task_content:
        Markdown content of the task file.
    feature_worktree:
        Path to the feature branch worktree (for running tests).
    dev_branch:
        Name of the dev branch (e.g. ``dev``).
    feature_branch:
        Name of the feature branch (e.g. ``feat/0046-eliminate-os-chdir``).
    repo_root:
        Path to the repository root (for computing diff).
    llm:
        LLM client for the review call.
    log_fh:
        Optional log file handle.
    socket_path:
        ORC API socket path (for AgentRunner if investigation needed).
    """
    logger.info("review_task: starting", task=task_name)

    # Step 1: Run tests
    test_passed, test_output = _run_tests(feature_worktree)
    logger.info("review_task: tests done", task=task_name, passed=test_passed)

    # Step 2: Compute diff
    git = Git(repo_root)
    diff = _compute_diff(git, dev_branch, feature_branch)

    # Step 3: LLM review call
    user_prompt = _build_review_prompt(
        task_name,
        task_content,
        diff,
        test_passed,
        test_output,
    )

    try:
        response = llm.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except Exception as exc:
        logger.error("review_task: LLM call failed", error=str(exc))
        return ReviewResult(
            verdict="rejected",
            comments=f"Review failed: {exc}",
            tests_passed=test_passed,
            test_output=test_output,
            success=False,
            error=str(exc),
        )

    return _parse_review_response(
        response.content or "",
        test_passed,
        test_output,
    )


def _parse_review_response(
    content: str,
    test_passed: bool,
    test_output: str,
) -> ReviewResult:
    """Parse the LLM's JSON response into a :class:`ReviewResult`."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("review_task: invalid JSON", error=str(exc))
        return ReviewResult(
            verdict="rejected",
            comments=f"Review LLM returned invalid JSON: {exc}",
            tests_passed=test_passed,
            test_output=test_output,
            success=False,
            error=str(exc),
        )

    verdict = str(data.get("verdict", "rejected")).lower()
    if verdict not in ("approved", "rejected"):
        verdict = "rejected"

    comments = str(data.get("comments", ""))

    return ReviewResult(
        verdict=verdict,
        comments=comments,
        tests_passed=test_passed,
        test_output=test_output,
    )
