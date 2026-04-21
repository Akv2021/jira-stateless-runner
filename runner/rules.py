"""Side-effecting rule handlers.

Per docs/ExternalRunner.md §4:

- ``rule1_unit_created``  — T1 seeding on Unit creation, with Difficulty fallback (M5).
- ``rule2_subtask_done``  — T2/T3/T4/T12/T13 dispatch on Sub-task Done (M6).
- ``rule4_stale_scan``    — T9 stale scan with Has Had Test lifetime guard (M7).

Rule 3 (T5/T6/T7/T8 timestamp writes) is NOT implemented here — it lives in
Jira Automation per docs/JiraImplementation.md §9.1.

Every rule follows the §6.6 partial-success contract: ``has_been_applied``
short-circuits the successor Sub-task write, but the audit comment is
verified via ``audit.comment_exists`` and re-posted on replays whose
prior run crashed between the field write and the comment POST. Rule 2
additionally performs an unconditional ``Last Subtask Completed At``
write per §5.5 step 2 (using the changelog event timestamp for
exactly-once semantics) — this fires even on NOOP dispatches so the
user-visible ``LSC`` field always tracks the most recent Done event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast

from runner import audit, idempotency
from runner.audit import TransitionEvent
from runner.config import RevisionGap, RevisionTarget, RevisionTargetDefault
from runner.jira_client import IssueNotFoundError
from runner.logging_ext import get_logger
from runner.models import ChangelogEvent, Outcome, TransitionID
from runner.state_machine import Lifecycle, SubtaskWorkType, UnitWorkType, dispatch

if TYPE_CHECKING:
    from runner.jira_client import JiraClient

_LOG = get_logger("runner.rules")


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

STALE_ELIGIBLE_FILTER: Final[str] = "IP-Stale-Eligible"
"""Saved Jira filter name provisioned in Phase 1.A.5 (JiraImplementation.md §9.2).

Rule 4 runs the Solo-profile version of this filter (``Has Had Test =
false``); the runner references it by name via ``filter = "X"`` JQL so
tuning happens in Jira without code changes.
"""

HAS_HAD_TEST_FIELD: Final[str] = "Has Had Test"
"""Durable lifetime-idempotency Boolean set once by T9 (§5.3, §9.2).

Set-once-never-cleared: T5/T11 upgrade, T7 Archive, T13 Regress all
leave the flag intact so a Unit never re-enters the stale-scan pool.
"""

_DEFAULT_STORY_POINTS: Final[dict[str, int]] = {
    "learn": 2,
    "revise": 1,
    "test": 2,
}
"""Per-subtask-kind default Story Points written by Rules 1, 2 and 4.

Centralised so tuning happens in one place; the label carried on the
Sub-task (``learn`` / ``revise`` / ``test``) is also the map key.
"""


def default_story_points(kind: str) -> int:
    """Return the spec default Story Points for a Subtask kind label.

    ``kind`` must be one of ``learn``, ``revise`` or ``test`` — the
    same token emitted as a Sub-task label. Unknown kinds raise
    ``KeyError`` rather than silently defaulting so typos surface in
    tests rather than in production velocity analytics.
    """
    return _DEFAULT_STORY_POINTS[kind]


def _read_field(payload: dict[str, Any], name: str) -> Any:
    """Return ``payload["fields"][name]`` or ``None`` if absent.

    Payloads come from ``JiraClient.get_issue`` / ``search_issues``,
    which rewrite ``customfield_XXXXX`` IDs to display names (M8 read-
    side translation), so callers address every field — system or
    custom — by its human-readable name.
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
    deleted-mid-flight 404s (§6.1), and idempotent replays. All
    side-effects (Sub-task creation, ``Revision Target`` seed, audit
    comment) are gated by the ``idem:<hex>`` label check so the second
    invocation on identical ``(unit, event, T1)`` inputs is a no-op;
    the §6.6 audit-comment re-post path re-emits the comment if a prior
    run crashed after the Sub-task + field writes but before the POST.
    """
    if not (event.is_new_issue and event.issuetype in UNIT_ISSUE_TYPES):
        return None

    try:
        payload = await client.get_issue(event.issue_key)
    except IssueNotFoundError:
        _LOG.warning(
            "issue_not_found_skip",
            extra={"issue_key": event.issue_key, "event_id": event.id, "rule": "rule1"},
        )
        return None
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
    summary = _summary(payload)
    audit_event = TransitionEvent(
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
    )
    if await idempotency.has_been_applied(_UnitRef(key=event.issue_key), key, client.count_issues):
        if not await audit.comment_exists(event.issue_key, key, client):
            _LOG.warning(
                "audit_repost",
                extra={"unit": event.issue_key, "transition": "T1", "idem_key": key},
            )
            await audit.post(event.issue_key, audit_event, client)
        return None

    await client.create_subtask(
        parent_key=event.issue_key,
        summary=f"[{stage}][Learn] \u2014 {summary}".strip(),
        labels=["learn", idempotency.label_for(key)],
        story_points=default_story_points("learn"),
    )
    await client.update_issue(event.issue_key, {"Revision Target": rev_target})
    await audit.post(event.issue_key, audit_event, client)
    return "T1"


@dataclass(frozen=True)
class _Rule2Plan:
    """Pre-computed side-effect bundle for one T2/T3/T4/T12/T13 transition.

    Splitting planning from execution keeps the audit-comment payload
    available for the §6.6 row 3→4 recovery path: when ``has_been_applied``
    short-circuits the successor/update writes, the caller still has
    the exact ``TransitionEvent`` needed to re-post a missing comment.
    """

    updates: dict[str, Any]
    audit_event: TransitionEvent
    successor_index: int  # 0 means no successor Sub-task (T4); else Revise#index
    successor_due: date | None


def _build_rule2_plan(
    *,
    transition: TransitionID,
    unit_summary: str,
    stage: str,
    rev_done: int,
    rev_target: int,
    now: datetime,
    event_id: int,
    run_id: int,
    key: str,
) -> _Rule2Plan:
    """Compute updates + audit event for ``transition`` without any I/O.

    Regress-first ordering (§5.5) is enforced here: the post-state tuple
    is derived up-front so the successor ``Revise#1`` surfaces on T12/T13
    regardless of the pre-state ``RevisionDone`` value.
    """
    del unit_summary  # referenced by caller via _execute_rule2_plan
    now_iso = now.isoformat()
    updates: dict[str, Any] = {"Last Transitioned At": now_iso}
    audit_kwargs: dict[str, Any] = {"revision_target": rev_target}
    successor_index = 0
    successor_due: date | None = None

    if transition == "T2":
        successor_due = _add_business_days(now.date(), RevisionGap[0])
        successor_index = 1
        updates["Work Type"] = "Revise"
        audit_kwargs.update(
            source_label="Learn",
            target_label="Revise#1",
            revision_done_pre=rev_done,
            revision_done_post=rev_done,
            outcome=None,
            due_date=successor_due,
            gap_index=1,
            gap_bd=RevisionGap[0],
        )
    elif transition == "T3":
        k = rev_done + 1
        next_idx = k + 1
        gap_bd = RevisionGap[next_idx - 1]
        successor_due = _add_business_days(now.date(), gap_bd)
        successor_index = next_idx
        updates["Revision Done"] = rev_done + 1
        audit_kwargs.update(
            source_label=f"Revise#{k}",
            target_label=f"Revise#{next_idx}",
            revision_done_pre=rev_done,
            revision_done_post=rev_done + 1,
            outcome="Pass",
            due_date=successor_due,
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
        successor_due = _add_business_days(now.date(), RevisionGap[0])
        successor_index = 1
        updates["Revision Done"] = 0
        audit_kwargs.update(
            source_label=f"Revise#{k}",
            target_label="Revise#1",
            revision_done_pre=rev_done,
            revision_done_post=0,
            outcome="Regress",
            due_date=successor_due,
            gap_index=1,
            gap_bd=RevisionGap[0],
        )
    else:  # T13
        successor_due = _add_business_days(now.date(), RevisionGap[0])
        successor_index = 1
        updates["Work Type"] = "Revise"
        updates["Revision Done"] = 0
        audit_kwargs.update(
            source_label="Test",
            target_label="Revise#1",
            revision_done_pre=rev_done,
            revision_done_post=0,
            outcome="Regress",
            due_date=successor_due,
            gap_index=1,
            gap_bd=RevisionGap[0],
        )

    audit_event = TransitionEvent(
        transition_id=transition,
        run_id=run_id,
        event_id=event_id,
        key=key,
        **audit_kwargs,
    )
    return _Rule2Plan(
        updates=updates,
        audit_event=audit_event,
        successor_index=successor_index,
        successor_due=successor_due,
    )


async def _execute_rule2_plan(
    *,
    client: JiraClient,
    unit_key: str,
    unit_summary: str,
    stage: str,
    plan: _Rule2Plan,
    key: str,
) -> None:
    """Apply ``plan`` in §6.6 order: successor Sub-task → Unit updates → audit."""
    if plan.successor_index > 0 and plan.successor_due is not None:
        idem_label = idempotency.label_for(key)
        await _spawn_revise(
            client,
            unit_key,
            stage,
            unit_summary,
            plan.successor_index,
            plan.successor_due,
            idem_label,
        )
    await client.update_issue(unit_key, plan.updates)
    await audit.post(unit_key, plan.audit_event, client)


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
        story_points=default_story_points("revise"),
        extra_fields={"duedate": due.isoformat(), "Work Type": "Revise"},
    )


_RULE2_TRANSITIONS: frozenset[str] = frozenset({"T2", "T3", "T4", "T12", "T13"})
"""Transitions Rule 2 is responsible for executing (ExternalRunner.md §4.1).

Dispatch may also return ``NOOP`` (Test-Pass maintenance, dangling
Paused/Archived completions) — those short-circuit with no writes.
"""


async def _write_lsc_once(
    client: JiraClient,
    parent_key: str,
    event: ChangelogEvent,
) -> None:
    """Stamp ``Last Subtask Completed At`` using the event timestamp (§5.5 step 2).

    Unconditional per §5.5: fires on every observed Sub-task → Done
    changelog event, including NOOP dispatches (Test-Pass maintenance,
    dangling Paused/Archived completions) that never reach the
    transition-specific side-effect block. Replay safety is inherent —
    the value is derived from ``event.created`` (not ``now``), so a
    second run writes the identical timestamp and Jira's set-to-value
    semantics make the PUT idempotent.
    """
    stamp = event.created.isoformat()
    await client.update_issue(parent_key, {"Last Subtask Completed At": stamp})


async def rule2_subtask_done(
    event: ChangelogEvent,
    client: JiraClient,
    *,
    run_id: int,
    now: datetime | None = None,
) -> TransitionID | None:
    """Handle a Sub-task ``→ Done`` event per ExternalRunner.md §4.1.

    Reads the parent Unit, writes the unconditional ``Last Subtask
    Completed At`` stamp (§5.5 step 2; fires even on NOOP dispatches),
    invokes ``state_machine.dispatch`` with the Regress-first ordering
    already baked in at §5.5, and applies the matching transition's
    side-effects (successor Sub-task creation, post-state tuple write,
    audit comment). Returns the applied ``TransitionID`` or ``None`` for
    any silent-skip path: non-Sub-task events, deleted-mid-flight 404s
    (§6.1), missing parent, missing tuple fields, NOOP dispatch, or
    idempotent replay. On replay the §6.6 row 3→4 recovery re-posts the
    audit comment if absent while leaving the already-correct Sub-task
    and field writes untouched.
    """
    if not (event.is_status_change_to_done and event.issuetype == SUBTASK_ISSUE_TYPE):
        return None

    stamp = now if now is not None else _utc_now()
    try:
        subtask_payload = await client.get_issue(event.issue_key)
    except IssueNotFoundError:
        _LOG.warning(
            "issue_not_found_skip",
            extra={"issue_key": event.issue_key, "event_id": event.id, "rule": "rule2"},
        )
        return None
    parent_key = _parent_key(subtask_payload)
    if parent_key is None:
        return None
    s_wt = _option_str(subtask_payload, "Work Type")
    if s_wt not in ("Learn", "Revise", "Test"):
        return None

    try:
        unit_payload = await client.get_issue(parent_key)
    except IssueNotFoundError:
        _LOG.warning(
            "issue_not_found_skip",
            extra={"issue_key": parent_key, "event_id": event.id, "rule": "rule2"},
        )
        return None

    # §5.5 step 2: unconditional LSC write on every Sub-task Done event,
    # independent of dispatch outcome. Using `event.created` makes the
    # write exactly-once under replay even without a Jira label guard.
    await _write_lsc_once(client, parent_key, event)

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
    plan = _build_rule2_plan(
        transition=transition,
        unit_summary=_summary(unit_payload),
        stage=stage,
        rev_done=rev_done,
        rev_target=rev_target,
        now=stamp,
        event_id=event.id,
        run_id=run_id,
        key=key,
    )
    if await idempotency.has_been_applied(_UnitRef(key=parent_key), key, client.count_issues):
        if not await audit.comment_exists(parent_key, key, client):
            _LOG.warning(
                "audit_repost",
                extra={"unit": parent_key, "transition": transition, "idem_key": key},
            )
            await audit.post(parent_key, plan.audit_event, client)
        return None

    await _execute_rule2_plan(
        client=client,
        unit_key=parent_key,
        unit_summary=_summary(unit_payload),
        stage=stage,
        plan=plan,
        key=key,
    )
    return transition


async def rule4_stale_scan(
    client: JiraClient,
    *,
    run_id: int,
    now: datetime | None = None,
    filter_name: str = STALE_ELIGIBLE_FILTER,
) -> list[str]:
    """Fire T9 for every Unit matching ``IP-Stale-Eligible`` (ExternalRunner.md §4.2).

    Returns the list of parent Unit keys for which a Test Sub-task was
    created this run. Lifetime idempotency is enforced by the
    ``Has Had Test = true`` clause baked into the saved filter (§9.2):
    once Rule 4 sets the flag, the Unit drops out of the candidate pool
    permanently and a second scan on the same calendar is a no-op.
    Rule 4 never writes tuple fields per §4.2.
    """
    stamp = now if now is not None else _utc_now()
    due = _add_business_days(stamp.date(), RevisionGap[0])
    jql = f'filter = "{filter_name}"'
    candidates = await client.search_issues(
        jql, fields=["summary", "Stage", HAS_HAD_TEST_FIELD], max_results=50
    )
    processed: list[str] = []
    for payload in candidates:
        unit_key = payload.get("key")
        if not isinstance(unit_key, str):
            continue
        stage = _option_str(payload, "Stage")
        if stage is None:
            continue
        summary = _summary(payload)
        # T9 idempotency is deterministic on unit_key alone (not run_id)
        # so retries after partial failure re-use the same idem label and
        # short-circuit via has_been_applied instead of creating a
        # duplicate Test subtask (§6.6 row 1->2 recovery).
        key = idempotency.compute_key(unit_key, "stale-scan", "T9")
        audit_event = TransitionEvent(
            transition_id="T9",
            source_label="StaleScan",
            target_label="Test",
            revision_done_pre=0,
            revision_done_post=0,
            revision_target=0,
            run_id=run_id,
            event_id=f"stale-{run_id}",
            key=key,
            due_date=due,
            gap_index=0,
            gap_bd=RevisionGap[0],
        )
        if await idempotency.has_been_applied(_UnitRef(unit_key), key, client.count_issues):
            if not await audit.comment_exists(unit_key, key, client):
                _LOG.warning(
                    "audit_repost",
                    extra={"unit": unit_key, "transition": "T9", "idem_key": key},
                )
                await audit.post(unit_key, audit_event, client)
            continue
        await client.create_subtask(
            parent_key=unit_key,
            summary=f"[{stage}][Test] \u2014 {summary}".strip(),
            labels=["test", idempotency.label_for(key)],
            story_points=default_story_points("test"),
            extra_fields={"duedate": due.isoformat(), "Work Type": "Test"},
        )
        await client.update_issue(unit_key, {HAS_HAD_TEST_FIELD: True})
        await audit.post(unit_key, audit_event, client)
        processed.append(unit_key)
    return processed


__all__ = [
    "HAS_HAD_TEST_FIELD",
    "STALE_ELIGIBLE_FILTER",
    "SUBTASK_ISSUE_TYPE",
    "UNIT_ISSUE_TYPES",
    "default_story_points",
    "rule1_unit_created",
    "rule2_subtask_done",
    "rule4_stale_scan",
]
