"""Pure-function dispatch table for T1–T13 transitions.

Per docs/ExternalRunner.md §2.3: ``dispatch(state, outcome) -> TransitionID``
takes a state tuple and Outcome, returns a TransitionID (or NOOP). Zero I/O,
zero side-effects. The 23-row decision matrix is authored in
docs/ImplementationTestMatrix.md §3 and verified by the parametric test
suite in tests/test_state_machine.py (M1).

M1 adds a ``NotImplementedError`` stub here against which 23 RED tests are
asserted; M2 implements the ``match/case`` body.
"""
