"""Pure-function dispatch table for T1-T13 transitions.

Per ExternalRunner.md §2.3: ``dispatch(...) -> TransitionID`` takes the
4-tuple (S.WorkType, U.Lifecycle, U.WorkType, S.Outcome) plus the
(RevisionDone, RevisionTarget) pair needed by branches 4/5 of the §5.5
dispatch rule, and returns a TransitionID (T2/T3/T4/T12/T13) or the
sentinel "NOOP" for dangling / Test-Pass maintenance rows. Zero I/O,
zero side-effects. The 23-row decision matrix is authored in
ImplementationTestMatrix.md §3 and verified by the parametric test suite
in tests/test_state_machine.py.
"""

from __future__ import annotations

from typing import Literal

SubtaskWorkType = Literal["Learn", "Revise", "Test"]
"""WorkType field on a Subtask (LivingRequirements.md §4)."""

UnitWorkType = Literal["Learn", "Revise"]
"""WorkType field on a Unit (LivingRequirements.md §5.2)."""

Lifecycle = Literal["Active", "Paused", "Archived"]
"""Unit Lifecycle (LivingRequirements.md §5.3)."""

Outcome = Literal["Pass", "Regress"]
"""Subtask Outcome on Revise/Test Subtasks (LivingRequirements.md §4).

``None`` on Learn Subtasks — a failed Learn is modelled by leaving the
Subtask incomplete rather than marking an Outcome.
"""

TransitionID = Literal[
    "T1",
    "T2",
    "T3",
    "T4",
    "T5",
    "T6",
    "T7",
    "T8",
    "T9",
    "T10",
    "T11",
    "T12",
    "T13",
    "NOOP",
]
"""All declared state transitions (T1-T13) plus the explicit NOOP sentinel.

Per LivingRequirements.md §5.2; NOOP is used by dispatch for branches 6
(Test-Pass maintenance) and 7 (dangling) where no Unit-side transition
fires but the calling Rule still needs a total function for its
match/case table.
"""


def dispatch(
    s_work_type: SubtaskWorkType,
    u_lifecycle: Lifecycle,
    u_work_type: UnitWorkType,
    s_outcome: Outcome | None,
    revision_done: int,
    revision_target: int,
) -> TransitionID:
    """Return the TransitionID for a Subtask → Done event.

    Inputs mirror the columns of ImplementationTestMatrix.md §3:

    - ``s_work_type`` — Subtask.WorkType (Learn / Revise / Test)
    - ``u_lifecycle`` — Unit.Lifecycle (Active / Paused / Archived)
    - ``u_work_type`` — Unit.WorkType (Learn / Revise)
    - ``s_outcome``   — Subtask.Outcome; ``None`` on Learn Subtasks
    - ``revision_done``, ``revision_target`` — integers compared as
      ``revision_done + 1`` against ``revision_target`` to split the
      Revise-Pass case into T3 (next) vs T4 (auto-Pause).

    Returns one of T2, T3, T4, T12, T13, or "NOOP". Pure — no I/O, no
    logging, no mutation of inputs.
    """
    key = (s_work_type, u_lifecycle, u_work_type, s_outcome)
    match key:
        case ("Revise", "Active", "Revise", "Regress"):
            return "T12"
        case ("Test", "Active", _, "Regress"):
            return "T13"
        case ("Learn", "Active", "Learn", _):
            return "T2"
        case ("Revise", "Active", "Revise", "Pass"):
            if revision_done + 1 < revision_target:
                return "T3"
            if revision_done + 1 == revision_target:
                return "T4"
            return "NOOP"
        case _:
            return "NOOP"
