"""Parametric dispatch test suite - 23 rows (D1-D23).

Ports ImplementationTestMatrix.md §3 "Dispatch Branch Matrix" row-by-row
into assertions against runner.state_machine.dispatch(). The tuple
(s_work_type, u_lifecycle, u_work_type, s_outcome) mirrors the first
four matrix columns; (revision_done, revision_target) satisfies the
"Extra guard" column that splits Revise-Pass into T3 (n+1 < Target) and
T4 (n+1 = Target). The matrix's "-" (maintenance / dangling) entries
map to the "NOOP" sentinel per runner.state_machine.TransitionID.

M1 RED baseline: every row fails with NotImplementedError from the
dispatch stub. M2 replaces the body with the match/case table and the
suite goes GREEN without edits here.

For rows where the matrix marks a column as "any" or "n/a":
- Learn Subtasks (D9-D14) use s_outcome=None (n/a per §4).
- Test/Revise Subtasks on non-Active Units (D5-D8, D20-D23) pin
  s_outcome="Pass" as a representative; §5.3 disables the regress path
  on non-Active Lifecycles so both values collapse to the same row.
- revision_done / revision_target are only consulted by D17/D18; other
  rows use placeholder (0, 1) values.
"""

from __future__ import annotations

import pytest

from runner.state_machine import (
    Lifecycle,
    Outcome,
    SubtaskWorkType,
    TransitionID,
    UnitWorkType,
    dispatch,
)

DISPATCH_MATRIX = [
    # Branch 6 (Test Pass on Active) and Branch 2 (Test Regress) rows
    pytest.param("Test", "Active", "Learn", "Pass", 0, 1, "NOOP", id="D1"),
    pytest.param("Test", "Active", "Learn", "Regress", 0, 1, "T13", id="D2"),
    pytest.param("Test", "Active", "Revise", "Pass", 0, 1, "NOOP", id="D3"),
    pytest.param("Test", "Active", "Revise", "Regress", 2, 5, "T13", id="D4"),
    # Branch 7 (dangling, Lifecycle != Active) for Test Subtasks
    pytest.param("Test", "Paused", "Learn", "Pass", 0, 1, "NOOP", id="D5"),
    pytest.param("Test", "Paused", "Revise", "Pass", 0, 1, "NOOP", id="D6"),
    pytest.param("Test", "Archived", "Learn", "Pass", 0, 1, "NOOP", id="D7"),
    pytest.param("Test", "Archived", "Revise", "Pass", 0, 1, "NOOP", id="D8"),
    # Branch 3 (Learn -> Revise) and dangling Learn rows
    pytest.param("Learn", "Active", "Learn", None, 0, 1, "T2", id="D9"),
    pytest.param("Learn", "Active", "Revise", None, 0, 1, "NOOP", id="D10"),
    pytest.param("Learn", "Paused", "Learn", None, 0, 1, "NOOP", id="D11"),
    pytest.param("Learn", "Paused", "Revise", None, 0, 1, "NOOP", id="D12"),
    pytest.param("Learn", "Archived", "Learn", None, 0, 1, "NOOP", id="D13"),
    pytest.param("Learn", "Archived", "Revise", None, 0, 1, "NOOP", id="D14"),
    # Branches 4/5/1 (Revise Pass/Regress) and post-T11 orphans
    pytest.param("Revise", "Active", "Learn", "Pass", 0, 1, "NOOP", id="D15"),
    pytest.param("Revise", "Active", "Learn", "Regress", 0, 1, "NOOP", id="D16"),
    pytest.param("Revise", "Active", "Revise", "Pass", 0, 2, "T3", id="D17"),
    pytest.param("Revise", "Active", "Revise", "Pass", 0, 1, "T4", id="D18"),
    pytest.param("Revise", "Active", "Revise", "Regress", 2, 5, "T12", id="D19"),
    # Branch 7 (dangling, Lifecycle != Active) for Revise Subtasks
    pytest.param("Revise", "Paused", "Learn", "Pass", 0, 1, "NOOP", id="D20"),
    pytest.param("Revise", "Paused", "Revise", "Pass", 0, 1, "NOOP", id="D21"),
    pytest.param("Revise", "Archived", "Learn", "Pass", 0, 1, "NOOP", id="D22"),
    pytest.param("Revise", "Archived", "Revise", "Pass", 0, 1, "NOOP", id="D23"),
]


@pytest.mark.parametrize(
    (
        "s_work_type",
        "u_lifecycle",
        "u_work_type",
        "s_outcome",
        "revision_done",
        "revision_target",
        "expected",
    ),
    DISPATCH_MATRIX,
)
def test_dispatch_matrix(
    s_work_type: SubtaskWorkType,
    u_lifecycle: Lifecycle,
    u_work_type: UnitWorkType,
    s_outcome: Outcome | None,
    revision_done: int,
    revision_target: int,
    expected: TransitionID,
) -> None:
    """Assert dispatch() returns the TransitionID the matrix predicts."""
    assert (
        dispatch(
            s_work_type,
            u_lifecycle,
            u_work_type,
            s_outcome,
            revision_done,
            revision_target,
        )
        == expected
    )
