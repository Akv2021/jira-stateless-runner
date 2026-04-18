"""Side-effecting rule handlers.

Per docs/ExternalRunner.md §4:

- ``rule1_unit_created``  — T1 seeding on Unit creation, with Difficulty fallback (M5).
- ``rule2_subtask_done``  — T2/T3/T4/T12/T13 dispatch on Sub-task Done (M6).
- ``rule4_stale_scan``    — T9 stale scan with Has Had Test lifetime guard (M7).

Rule 3 (T5/T6/T7/T8 timestamp writes) is NOT implemented here — it lives in
Jira Automation per docs/JiraImplementation.md §9.1.

M5 SCOPE NOTE: only ``rule1_unit_created`` and its supporting helpers land
in this module at M5. Cross-event classification (M6), stale-scan JQL (M7),
and the System-Config watermark read/write (M8) are explicitly out-of-scope
and land in subsequent milestones per docs/ImplementationRoadmap.md §M5-M8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from runner import audit, idempotency
from runner.audit import TransitionEvent
from runner.config import RevisionTarget, RevisionTargetDefault
from runner.models import ChangelogEvent, TransitionID

if TYPE_CHECKING:
    from runner.jira_client import JiraClient


@dataclass(frozen=True)
class _UnitRef:
    """Minimal ``HasIssueKey``-compatible stub for idempotency lookups."""

    key: str


UNIT_ISSUE_TYPES: Final[frozenset[str]] = frozenset(
    {"Problem", "Concept", "Implementation", "Pattern", "Debug"}
)
"""Jira issue-type names that represent Units (ExternalRunner.md §2.1)."""

_FALLBACK_NOTE: Final[str] = "Difficulty missing at creation; RevisionTarget defaulted to 2 (Easy)."
"""§4.1 audit-comment addendum emitted when the Difficulty fallback fires."""


def _read_field(payload: dict[str, Any], name: str) -> Any:
    """Return ``payload["fields"][name]`` or ``None`` if absent.

    The M8 bootstrap self-check will normalise custom-field IDs into
    display names; until then callers (tests, future M8 wiring) must
    supply a payload already keyed by the human-readable field name.
    """
    fields = payload.get("fields") or {}
    return fields.get(name)


def _stage(payload: dict[str, Any]) -> str | None:
    value = _read_field(payload, "Stage")
    if isinstance(value, dict):
        name = value.get("value") or value.get("name")
        return name if isinstance(name, str) else None
    return value if isinstance(value, str) else None


def _difficulty(payload: dict[str, Any]) -> str | None:
    value = _read_field(payload, "Difficulty")
    if isinstance(value, dict):
        name = value.get("value") or value.get("name")
        return name if isinstance(name, str) else None
    return value if isinstance(value, str) else None


def _summary(payload: dict[str, Any]) -> str:
    value = _read_field(payload, "summary")
    return value if isinstance(value, str) else ""


async def rule1_unit_created(
    event: ChangelogEvent,
    client: JiraClient,
    *,
    run_id: int,
) -> TransitionID | None:
    """Handle a Unit-creation changelog event per ExternalRunner.md §4.1.

    Returns the applied ``TransitionID`` (``"T1"``) on a fresh
    execution, or ``None`` for any silent-skip path: non-creation
    events, non-Unit issue-types, Stage-missing pre-state violation,
    and idempotent replays. All side-effects (Sub-task creation,
    ``Revision Target`` seed, audit comment) are gated by the
    ``idem:<hex>`` label check so the second invocation on identical
    ``(unit, event, T1)`` inputs is a no-op.
    """
    if not (event.is_new_issue and event.issuetype in UNIT_ISSUE_TYPES):
        return None

    payload = await client.get_issue(event.issue_key)
    stage = _stage(payload)
    if stage is None:
        return None

    difficulty = _difficulty(payload)
    if difficulty in RevisionTarget:
        rev_target = RevisionTarget[difficulty]
        note: str | None = None
    else:
        rev_target = RevisionTargetDefault
        note = _FALLBACK_NOTE

    key = idempotency.compute_key(event.issue_key, str(event.id), "T1")
    if await idempotency.has_been_applied(_UnitRef(key=event.issue_key), key, client.count_issues):
        return None

    summary = _summary(payload)
    await client.create_subtask(
        parent_key=event.issue_key,
        summary=f"[{stage}][Learn] \u2014 {summary}".strip(),
        labels=["learn", idempotency.label_for(key)],
        story_points=2,
    )
    await client.update_issue(event.issue_key, {"Revision Target": rev_target})
    await audit.post(
        event.issue_key,
        TransitionEvent(
            transition_id="T1",
            source_label=f"Create({stage})",
            target_label="Learn#1",
            revision_done_pre=0,
            revision_done_post=0,
            revision_target=rev_target,
            run_id=run_id,
            event_id=event.id,
            key=key,
            note=note,
        ),
        client,
    )
    return "T1"


__all__ = ["UNIT_ISSUE_TYPES", "rule1_unit_created"]
