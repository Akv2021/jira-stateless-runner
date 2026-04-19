"""Layer-2 audit-comment formatting and emission on the parent Unit.

Per docs/ExternalRunner.md §5.2, one comment is posted on the parent
Unit (not the Subtask) per executed transition. The canonical 5-line
template is::

    [Runner][T2] Learn#1 → Revise#1
      RevisionDone: 0 → 0 (target 3)
      Outcome: Pass
      DueDate(Revise#1): 2026-04-22  (RevisionGap[1] = 2bd)
      run: 7241 · event: 12345678 · key: idem_8c4d2a1f9bb7

Byte-exact details: 2-space indent on continuation lines; two spaces
before the ``(RevisionGap[…])`` parenthetical; U+00B7 MIDDLE DOT (``·``)
between footer fields; U+2192 RIGHTWARDS ARROW (``→``) in the header.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from runner.idempotency import display_for
from runner.models import Outcome, TransitionID

if TYPE_CHECKING:
    from runner.jira_client import JiraClient

_ARROW = "\u2192"
_MIDDOT = "\u00b7"


@dataclass(frozen=True)
class TransitionEvent:
    """Data carrier describing one executed transition for audit emission.

    Populated by ``runner.rules`` (M5+); consumed by ``format_comment``
    to render the §5.2 template. Optional fields (``outcome``,
    ``due_date``, ``gap_index``, ``gap_bd``) cover the cases where the
    transition has no successor Subtask (T4/T7/T8) or no user-asserted
    outcome (T1). ``key`` carries the 12-hex digest from
    ``runner.idempotency.compute_key``.
    """

    transition_id: TransitionID
    source_label: str
    target_label: str
    revision_done_pre: int
    revision_done_post: int
    revision_target: int
    run_id: int
    event_id: int | str
    key: str
    outcome: Outcome | None = None
    due_date: date | None = None
    gap_index: int | None = None
    gap_bd: int | None = None
    note: str | None = None


def format_comment(event: TransitionEvent) -> str:
    """Render ``event`` into the canonical §5.2 audit-comment string.

    Output is deterministic and byte-identical for equal inputs; lines
    that do not apply to the transition (missing outcome, no successor
    Subtask → no DueDate line) are omitted rather than emitted as empty
    placeholders. The final footer line is always present.
    """
    lines: list[str] = [
        f"[Runner][{event.transition_id}] {event.source_label} {_ARROW} {event.target_label}",
        f"  RevisionDone: {event.revision_done_pre} "
        f"{_ARROW} {event.revision_done_post} (target {event.revision_target})",
    ]
    if event.outcome is not None:
        lines.append(f"  Outcome: {event.outcome}")
    if event.due_date is not None and event.gap_index is not None and event.gap_bd is not None:
        lines.append(
            f"  DueDate({event.target_label}): {event.due_date.isoformat()}"
            f"  (RevisionGap[{event.gap_index}] = {event.gap_bd}bd)"
        )
    if event.note is not None:
        lines.append(f"  Note: {event.note}")
    lines.append(
        f"  run: {event.run_id} {_MIDDOT} event: {event.event_id} "
        f"{_MIDDOT} key: {display_for(event.key)}"
    )
    return "\n".join(lines)


async def post(unit_key: str, event: TransitionEvent, client: JiraClient) -> None:
    """Format ``event`` and POST the resulting comment onto ``unit_key``.

    Thin async wrapper around ``format_comment`` + ``JiraClient.post_comment``.
    Callers (``runner.rules`` in M5+) are responsible for gating this
    call with ``runner.idempotency.has_been_applied`` so replays do not
    produce duplicate comments.
    """
    body = format_comment(event)
    await client.post_comment(unit_key, body)


__all__ = ["TransitionEvent", "format_comment", "post"]
