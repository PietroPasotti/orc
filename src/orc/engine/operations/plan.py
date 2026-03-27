"""Plan operation — structured LLM call replacing the planner agent.

Reads a vision document and produces a list of task specifications via a
single LLM call with ``response_format: json_object``.  The orchestrator
then creates tasks on the board directly — no agent loop, no tool calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from orc.ai.llm import LLMClient
from orc.coordination.models import TaskBody

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSpec:
    """A task specification produced by the planner LLM call."""

    title: str
    """Short dash-separated title, e.g. ``add-user-auth``."""

    overview: str
    """Description of what the task accomplishes."""

    in_scope: list[str] = field(default_factory=list)
    """Items explicitly in scope."""

    out_of_scope: list[str] = field(default_factory=list)
    """Items explicitly out of scope."""

    steps: list[str] = field(default_factory=list)
    """Ordered implementation steps."""

    notes: str = ""
    """Optional notes for the coder."""

    def to_task_body(self) -> TaskBody:
        """Convert to a :class:`~orc.coordination.models.TaskBody`."""
        return TaskBody(
            overview=self.overview,
            in_scope=self.in_scope,
            out_of_scope=self.out_of_scope,
            steps=self.steps,
            notes=self.notes,
        )


@dataclass(frozen=True)
class PlanResult:
    """Result of a plan operation."""

    tasks: list[TaskSpec]
    """Task specifications to create on the board."""

    vision_summary: str
    """2–4 sentence summary of the vision (for closing it)."""

    success: bool = True
    """Whether the plan operation completed successfully."""

    error: str = ""
    """Error message if ``success`` is ``False``."""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a technical project planner.  You receive a **vision document** \
describing a feature or change, plus a list of existing tasks on the board.

Your job:
1. Break the vision into 1–5 concrete, independently-implementable tasks.
2. Each task must be a self-contained unit of work that a single coder \
agent can complete in one session (roughly 30–100 iterations of code editing).
3. Avoid creating tasks that duplicate work already on the board.
4. Provide clear, actionable implementation steps.

Respond with a JSON object matching this schema exactly:

```json
{
  "tasks": [
    {
      "title": "short-dash-separated-title",
      "overview": "What this task accomplishes and why.",
      "in_scope": ["item 1", "item 2"],
      "out_of_scope": ["item 1"],
      "steps": ["Step 1: ...", "Step 2: ..."],
      "notes": "Optional notes for the coder."
    }
  ],
  "vision_summary": "2-4 sentence summary of the vision document."
}
```

Rules:
- ``title`` must be lowercase, dash-separated, no spaces, max 60 chars.
- Each task should touch a focused set of files.
- If the vision is trivial (single file change), create exactly 1 task.
- If the vision is large, split into logical phases that can merge independently.
- Do NOT include test-only tasks — tests are part of each implementation task.
"""


def _build_user_prompt(
    vision_name: str,
    vision_content: str,
    existing_tasks: list[str],
) -> str:
    """Build the user prompt for the planning LLM call."""
    parts = [
        f"## Vision: {vision_name}\n\n{vision_content}",
    ]
    if existing_tasks:
        task_list = "\n".join(f"- {t}" for t in existing_tasks)
        parts.append(f"\n## Existing tasks on the board\n\n{task_list}")
    else:
        parts.append("\n## Existing tasks on the board\n\nNone.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_vision(
    vision_name: str,
    vision_content: str,
    *,
    llm: LLMClient,
    existing_tasks: list[str] | None = None,
) -> PlanResult:
    """Plan tasks for a vision document via a single structured LLM call.

    Parameters
    ----------
    vision_name:
        Filename of the vision, e.g. ``0009-cwd-explicit.md``.
    vision_content:
        Full markdown content of the vision file.
    llm:
        LLM client to use for the planning call.
    existing_tasks:
        Names of tasks already on the board (for dedup).

    Returns
    -------
    PlanResult
        Parsed task specifications and vision summary.
    """
    user_prompt = _build_user_prompt(vision_name, vision_content, existing_tasks or [])

    logger.info("plan_vision: calling LLM", vision=vision_name)

    try:
        response = llm.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except Exception as exc:
        logger.error("plan_vision: LLM call failed", error=str(exc))
        return PlanResult(tasks=[], vision_summary="", success=False, error=str(exc))

    return _parse_plan_response(response.content or "")


def _parse_plan_response(content: str) -> PlanResult:
    """Parse the LLM's JSON response into a :class:`PlanResult`."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("plan_vision: invalid JSON from LLM", error=str(exc), content=content[:500])
        return PlanResult(
            tasks=[],
            vision_summary="",
            success=False,
            error=f"Invalid JSON from LLM: {exc}",
        )

    if not isinstance(data, dict):
        return PlanResult(
            tasks=[],
            vision_summary="",
            success=False,
            error=f"Expected JSON object, got {type(data).__name__}",
        )

    tasks: list[TaskSpec] = []
    for item in data.get("tasks", []):
        tasks.append(_parse_task_spec(item))

    vision_summary = data.get("vision_summary", "")

    if not tasks:
        return PlanResult(
            tasks=[],
            vision_summary=vision_summary,
            success=False,
            error="LLM returned zero tasks",
        )

    logger.info(
        "plan_vision: parsed tasks",
        count=len(tasks),
        titles=[t.title for t in tasks],
    )
    return PlanResult(tasks=tasks, vision_summary=vision_summary)


def _parse_task_spec(item: Any) -> TaskSpec:
    """Parse a single task spec from the LLM's JSON output."""
    if not isinstance(item, dict):
        return TaskSpec(title="unknown", overview=str(item))

    return TaskSpec(
        title=str(item.get("title", "untitled")).lower().replace(" ", "-")[:60],
        overview=str(item.get("overview", "")),
        in_scope=[str(s) for s in item.get("in_scope", [])],
        out_of_scope=[str(s) for s in item.get("out_of_scope", [])],
        steps=[str(s) for s in item.get("steps", [])],
        notes=str(item.get("notes", "")),
    )
