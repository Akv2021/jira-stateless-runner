"""Side-effecting rule handlers.

Per docs/ExternalRunner.md §4:

- ``rule1_unit_created``  — T1 seeding on Unit creation, with Difficulty fallback (M5).
- ``rule2_subtask_done``  — T2/T3/T4/T12/T13 dispatch on Sub-task Done (M6).
- ``rule4_stale_scan``    — T9 stale scan with Has Had Test lifetime guard (M7).

Rule 3 (T5/T6/T7/T8 timestamp writes) is NOT implemented here — it lives in
Jira Automation per docs/JiraImplementation.md §9.1.
"""
