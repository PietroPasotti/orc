"""Pydantic models for board.yaml data structures.

These models validate board data at read time so that invalid or missing
keys are caught early with a clear error rather than silently returning
garbage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskEntry(BaseModel):
    """A single open task entry on the board."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    status: str = ""
    assigned_to: str | None = None
    comments: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_string(cls, v: Any) -> Any:
        """Accept bare string entries (legacy format) by treating them as the task name."""
        if isinstance(v, str):
            return {"name": v}
        return v


class DoneTaskEntry(BaseModel):
    """A completed task entry on the board."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    commit_tag: str | None = Field(default=None, alias="commit-tag")
    timestamp: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_string(cls, v: Any) -> Any:
        """Accept bare string entries (legacy format) by treating them as the task name."""
        if isinstance(v, str):
            return {"name": v}
        return v

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_datetime(cls, v: Any) -> Any:
        """Accept datetime objects from YAML auto-parsing of unquoted ISO timestamps."""
        if isinstance(v, datetime):
            return v.isoformat()
        return v


class Board(BaseModel):
    """The full board.yaml structure."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    counter: int = 0
    open: list[TaskEntry] = Field(default_factory=list)
    done: list[DoneTaskEntry] = Field(default_factory=list)
