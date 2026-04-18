"""Side-effecting rule handlers.

Per docs/ExternalRunner.md §4:

- ``rule1_unit_created``  — T1 seeding on Unit creation, with Difficulty fallback (M5).
- ``rule2_subtask_done``  — T2/T3/T4/T12/T13 dispatch on Sub-task Done (M6).
- ``rule4_stale_scan``    — T9 stale scan with Has Had Test lifetime guard (M7).

Rule 3 (T5/T6/T7/T8 timestamp writes) is NOT implemented here — it lives in
Jira Automation per docs/JiraImplementation.md §9.1.

M6 SCOPE NOTE: Rule 2 writes only the post-state tuple fields, the
``Last Transitioned At`` / ``Last Subtask Completed At`` timestamps, and
(on T4) ``Paused At``. NOOP dispatches (Test-Pass maintenance, dangling
Paused/Archived completions) do NOT write ``LastSubTaskCompletedAt`` —
that §5.5 step-2 unconditional write is deferred to M8 polling-loop
wiring, where the triggering-subtask idempotency label keeps retries
safe. Partial-success recovery between ``create_subtask`` and
``update_issue`` (§6.6) is also deferred to M9.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast

from runner import audit, idempotency
from runner.audit import TransitionEvent
from runner.config import RevisionGap, RevisionTarget, RevisionTargetDefault
from runner.models import ChangelogEvent, Outcome, TransitionID
from runner.state_machine import Lifecycle, SubtaskWorkType, UnitWorkType, dispatch

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

SUBTASK_ISSUE_TYPE: Final[str] = "Sub-task"
"""Jira issue-type name for Subtasks (ExternalRunner.md §4.1 Rule 2 filter)."""

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


def _option_str(payload: dict[str, Any], name: str) -> str | None:
    """Read a Jira scalar / option-select field as ``str | None``.

    Handles both the ``{"value": "Medium"}`` option-object shape and
    the bare-string shape Jira returns depending on the custom-field
    schema; any other type maps to ``None``.
    """
    value = _read_field(payload, name)
    if isinstance(value, dict):
        v = value.get("value") or value.get("name")
        return v if isinstance(v, str) else None
    return value if isinstance(value, str) else None


def _int_field(payload: dict[str, Any], name: str) -> int | None:
    value = _read_field(payload, name)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    return None


def _parent_key(payload: dict[str, Any]) -> str | None:
    parent = _read_field(payload, "parent")
    if isinstance(parent, dict):
        key = parent.get("key")
        return key if isinstance(key, str) else None
    return None


def _summary(payload: dict[str, Any]) -> str:
    value = _read_field(payload, "summary")
    return value if isinstance(value, str) else ""


def _add_business_days(start: date, n: int) -> date:
    """Return ``start`` plus ``n`` Mon-Fri business days (weekends skipped)."""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


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
    stage = _option_str(payload, "Stage")
    if stage is None:
        return None

    difficulty = _option_str(payload, "Difficulty")
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


async def _apply_rule2(
    *,
    transition: TransitionID,
    client: JiraClient,
    unit_key: str,
    unit_summary: str,
    stage: str,
    rev_done: int,
    rev_target: int,
    now: datetime,
    event_id: int,
    run_id: int,
    key: str,
) -> None:
    """Execute the side-effects for one T2/T3/T4/T12/T13 transition.

    Regress-first ordering (§5.5) is enforced here: the post-state tuple
    is computed up-front, so the successor Sub-task for T12/T13 carries
    ``Revise#1`` in its title regardless of the pre-state ``RevisionDone``.
    Write order follows §6.6: Subtask create → Unit field edit → audit
    comment. The successor Subtask's ``idem:<key>`` label (and, for T4,
    the idempotent Unit ``Lifecycle=Paused`` post-state) guard replays.
    """
    idem_label = idempotency.label_for(key)
    now_iso = now.isoformat()
    updates: dict[str, Any] = {
        "Last Transitioned At": now_iso,
        "Last Subtask Completed At": now_iso,
    }
    audit_kwargs: dict[str, Any] = {"revision_target": rev_target}

    if transition == "T2":
        due = _add_business_days(now.date(), RevisionGap[0])
        await _spawn_revise(client, unit_key, stage, unit_summary, 1, due, idem_label)
        updates["Work Type"] = "Revise"
        audit_kwargs.update(
            source_label="Learn",
            target_label="Revise#1",
            revision_done_pre=rev_done,
            revision_done_post=rev_done,
            outcome=None,
            due_date=due,
            gap_index=1,
            gap_bd=RevisionGap[0],
        )
    elif transition == "T3":
        k = rev_done + 1
        next_idx = k + 1
        gap_bd = RevisionGap[next_idx - 1]
        due = _add_business_days(now.date(), gap_bd)
        await _spawn_revise(client, unit_key, stage, unit_summary, next_idx, due, idem_label)
        updates["Revision Done"] = rev_done + 1
        audit_kwargs.update(
            source_label=f"Revise#{k}",
            target_label=f"Revise#{next_idx}",
            revision_done_pre=rev_done,
            revision_done_post=rev_done + 1,
            outcome="Pass",
            due_date=due,
            gap_index=next_idx,
            gap_bd=gap_bd,
        )
    elif transition == "T4":
        k = rev_done + 1
        updates["Revision Done"] = rev_target
        updates["Lifecycle"] = "Paused"
        updates["Paused At"] = now_iso
        audit_kwargs.update(
            source_label=f"Revise#{k}",
            target_label="Paused",
            revision_done_pre=rev_done,
            revision_done_post=rev_target,
            outcome="Pass",
        )
    elif transition == "T12":
        k = rev_done + 1
        due = _add_business_days(now.date(), RevisionGap[0])
        await _spawn_revise(client, unit_key, stage, unit_summary, 1, due, idem_label)
        updates["Revision Done"] = 0
        audit_kwargs.update(
            source_label=f"Revise#{k}",
            target_label="Revise#1",
            revision_done_pre=rev_done,
            revision_done_post=0,
            outcome="Regress",
            due_date=due,
            gap_index=1,
            gap_bd=RevisionGap[0],
        )
    else:  # T13
        due = _add_business_days(now.date(), RevisionGap[0])
        await _spawn_revise(client, unit_key, stage, unit_summary, 1, due, idem_label)
        updates["Work Type"] = "Revise"
        updates["Revision Done"] = 0
        audit_kwargs.update(
            source_label="Test",
            target_label="Revise#1",
            revision_done_pre=rev_done,
            revision_done_post=0,
            outcome="Regress",
            due_date=due,
            gap_index=1,
            gap_bd=RevisionGap[0],
        )

    await client.update_issue(unit_key, updates)
    await audit.post(
        unit_key,
        TransitionEvent(
            transition_id=transition,
            run_id=run_id,
            event_id=event_id,
            key=key,
            **audit_kwargs,
        ),
        client,
    )


async def _spawn_revise(
    client: JiraClient,
    parent_key: str,
    stage: str,
    unit_summary: str,
    index: int,
    due: date,
    idem_label: str,
) -> None:
    """Create the successor ``[Stage][Revise#n]`` Sub-task under ``parent_key``."""
    await client.create_subtask(
        parent_key=parent_key,
        summary=f"[{stage}][Revise#{index}] \u2014 {unit_summary}".strip(),
        labels=["revise", idem_label],
        story_points=1,
        extra_fields={"duedate": due.isoformat(), "Work Type": "Revise"},
    )


_RULE2_TRANSITIONS: frozenset[str] = frozenset({"T2", "T3", "T4", "T12", "T13"})
"""Transitions Rule 2 is responsible for executing (ExternalRunner.md §4.1).

Dispatch may also return ``NOOP`` (Test-Pass maintenance, dangling
Paused/Archived completions) — those short-circuit with no writes.
"""


async def rule2_subtask_done(
    event: ChangelogEvent,
    client: JiraClient,
    *,
    run_id: int,
    now: datetime | None = None,
) -> TransitionID | None:
    """Handle a Sub-task ``→ Done`` event per ExternalRunner.md §4.1.

    Reads the parent Unit, invokes ``state_machine.dispatch`` with the
    Regress-first ordering already baked in at §5.5, and applies the
    matching transition's side-effects (successor Sub-task creation,
    post-state tuple write, audit comment). Returns the applied
    ``TransitionID`` or ``None`` for any silent-skip path: non-Sub-task
    events, missing parent, missing tuple fields, NOOP dispatch, or
    idempotent replay (``idem:<hex>`` label already present under the
    parent Unit).
    """
    if not (event.is_status_change_to_done and event.issuetype == SUBTASK_ISSUE_TYPE):
        return None

    stamp = now if now is not None else _utc_now()
    subtask_payload = await client.get_issue(event.issue_key)
    parent_key = _parent_key(subtask_payload)
    if parent_key is None:
        return None
    s_wt = _option_str(subtask_payload, "Work Type")
    if s_wt not in ("Learn", "Revise", "Test"):
        return None

    unit_payload = await client.get_issue(parent_key)
    stage = _option_str(unit_payload, "Stage")
    u_wt = _option_str(unit_payload, "Work Type")
    u_life = _option_str(unit_payload, "Lifecycle")
    rev_done = _int_field(unit_payload, "Revision Done")
    rev_target = _int_field(unit_payload, "Revision Target")
    if (
        stage is None
        or u_wt not in ("Learn", "Revise")
        or u_life not in ("Active", "Paused", "Archived")
        or rev_done is None
        or rev_target is None
    ):
        return None

    if s_wt == "Learn":
        outcome: str | None = None
    else:
        raw = _option_str(subtask_payload, "Outcome")
        outcome = raw if raw in ("Pass", "Regress") else "Pass"

    transition = dispatch(
        cast(SubtaskWorkType, s_wt),
        cast(Lifecycle, u_life),
        cast(UnitWorkType, u_wt),
        cast("Outcome | None", outcome),
        rev_done,
        rev_target,
    )
    if transition not in _RULE2_TRANSITIONS:
        return None

    key = idempotency.compute_key(parent_key, str(event.id), transition)
    if await idempotency.has_been_applied(_UnitRef(key=parent_key), key, client.count_issues):
        return None

    await _apply_rule2(
        transition=transition,
        client=client,
        unit_key=parent_key,
        unit_summary=_summary(unit_payload),
        stage=stage,
        rev_done=rev_done,
        rev_target=rev_target,
        now=stamp,
        event_id=event.id,
        run_id=run_id,
        key=key,
    )
    return transition


__all__ = [
    "SUBTASK_ISSUE_TYPE",
    "UNIT_ISSUE_TYPES",
    "rule1_unit_created",
    "rule2_subtask_done",
]
