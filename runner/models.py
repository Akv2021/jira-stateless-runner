"""Pydantic domain models for Jira entities consumed by the runner.

Per docs/ExternalRunner.md §2.2 and docs/ImplementationRoadmap.md M3:
``Unit``, ``Subtask``, ``ChangelogEvent``, plus the literal types
``TransitionID``, ``Outcome``, ``Stage``, ``Lifecycle``, ``WorkType``.

The models mirror the platform-agnostic vocabulary of
docs/LivingRequirements.md §2 (Glossary) and §4 (Domain Model). Mapping
from Jira REST payloads into these types is a responsibility of later
milestones (M5-M8); this module performs no I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from runner.state_machine import Lifecycle as Lifecycle
from runner.state_machine import Outcome as Outcome
from runner.state_machine import SubtaskWorkType as SubtaskWorkType
from runner.state_machine import TransitionID as TransitionID
from runner.state_machine import UnitWorkType as UnitWorkType

Stage = Literal["Beginner", "Intermediate", "Advanced"]
"""Unit depth level (LivingRequirements.md §2 Glossary)."""

Difficulty = Literal["Easy", "Medium", "Hard"]
"""Unit difficulty seeds ``RevisionTarget`` at T1 (FR4)."""

SubtaskStatus = Literal["Backlog", "Todo", "InProgress", "Done"]
"""Subtask status set (OQ-11; ``Backlog`` added v0.5)."""

WorkType = SubtaskWorkType
"""Roadmap M3 alias — the superset of Unit/Subtask WorkType values."""

_ISSUE_KEY_RE = r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$"

_BASE_CONFIG = ConfigDict(
    frozen=True,
    extra="ignore",
    str_strip_whitespace=True,
    populate_by_name=True,
)


class Unit(BaseModel):
    """Persistent Unit — the state-tuple bearer (LivingRequirements §5.1).

    The ``(stage, work_type, lifecycle, revision_done)`` 4-tuple plus
    ``revision_target`` is the full input required by
    ``runner.state_machine.dispatch``; the timestamp fields feed
    runtime projections (staleness via ``last_subtask_completed_at``;
    progress-velocity via ``last_transitioned_at``) per §5.6.
    """

    model_config = _BASE_CONFIG

    key: str = Field(..., pattern=_ISSUE_KEY_RE)
    stage: Stage
    work_type: UnitWorkType
    lifecycle: Lifecycle
    revision_done: int = Field(..., ge=0)
    revision_target: int = Field(..., ge=1, le=10)
    difficulty: Difficulty | None = None
    has_had_test: bool = False
    last_subtask_completed_at: datetime | None = None
    last_transitioned_at: datetime | None = None
    paused_at: datetime | None = None
    created_at: datetime | None = None


class Subtask(BaseModel):
    """Execution-record Subtask (LivingRequirements §4 Domain Model).

    ``outcome`` is populated only on Revise/Test Subtasks at ``→ Done``
    (default ``Pass``). Learn Subtasks carry no ``outcome`` — a failed
    Learn is modelled by leaving the Subtask incomplete (§4).
    """

    model_config = _BASE_CONFIG

    key: str = Field(..., pattern=_ISSUE_KEY_RE)
    parent_key: str = Field(..., pattern=_ISSUE_KEY_RE)
    work_type: SubtaskWorkType
    status: SubtaskStatus
    title: str = Field(..., min_length=1)
    due_date: datetime | None = None
    effort_points: int | None = Field(default=None, ge=0)
    outcome: Outcome | None = None
    completed_at: datetime | None = None


class ChangelogItem(BaseModel):
    """Single field-level delta inside a Jira changelog history entry.

    Accepts Jira's native ``fromString`` / ``toString`` payload keys via
    aliases so ``ChangelogEvent.model_validate(raw_jira_json)`` works
    without a pre-mapping step.
    """

    model_config = _BASE_CONFIG

    field: str = Field(..., min_length=1)
    from_value: str | None = Field(default=None, alias="fromString")
    to_value: str | None = Field(default=None, alias="toString")


class ChangelogEvent(BaseModel):
    """Single Jira changelog history entry (ExternalRunner.md §3.4).

    ``id`` is the monotonic integer watermark the runner tracks in the
    System Config issue (§3.4); ``items`` carries the field-level
    deltas that classify the event into ``issue_created`` /
    ``subtask_transitioned_to_done`` / ``ignored`` (see §4.1).
    """

    model_config = _BASE_CONFIG

    id: int = Field(..., ge=0)
    issue_key: str = Field(..., pattern=_ISSUE_KEY_RE)
    created: datetime
    author_account_id: str | None = None
    items: tuple[ChangelogItem, ...] = ()


__all__ = [
    "ChangelogEvent",
    "ChangelogItem",
    "Difficulty",
    "Lifecycle",
    "Outcome",
    "Stage",
    "Subtask",
    "SubtaskStatus",
    "SubtaskWorkType",
    "TransitionID",
    "Unit",
    "UnitWorkType",
    "WorkType",
]
